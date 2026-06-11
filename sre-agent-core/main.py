import os
import json
import logging
import shutil
import tempfile
import requests
import git
from datetime import datetime
from typing import Optional, Dict, Any
from fastapi import FastAPI, Request, Header, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("sre-agent-core.main")

try:
    # pyrefly: ignore [missing-import]
    from sre_agent_core.config import settings
except ImportError:
    try:
        from config import settings
    except ImportError:
        # Fallback settings class if not found
        class MockSettings:
            github_token = os.environ.get("GITHUB_TOKEN", "")
            github_webhook_secret = os.environ.get("GITHUB_WEBHOOK_SECRET")
            gemini_api_key = os.environ.get("GEMINI_API_KEY")
            port = int(os.environ.get("PORT", "8000"))
            host = os.environ.get("HOST", "0.0.0.0")
            workspace_dir = os.environ.get("WORKSPACE_DIR", "/tmp/sre-agent-workspace")
        settings = MockSettings()

try:
    from sre_agent_core.diagnostics import diagnose_and_repair, apply_modifications
except ImportError:
    from diagnostics import diagnose_and_repair, apply_modifications

try:
    from sre_agent_core.github_client import GitHubClientWrapper
except ImportError:
    from github_client import GitHubClientWrapper

from fastapi.middleware.cors import CORSMiddleware
from fastapi import Depends
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
import jwt
import httpx

try:
    from sre_agent_core.database import init_db, get_db, User, MonitoredRepo, HealingRunRecord, SessionLocal
except ImportError:
    from database import init_db, get_db, User, MonitoredRepo, HealingRunRecord, SessionLocal

app = FastAPI(
    title="SRE Self-Healing Agent Core",
    description="FastAPI application receiving GitHub workflow_run failure webhooks and triggering auto-healing workflows.",
    version="1.0.0"
)

@app.on_event("startup")
def startup_event():
    logger.info("Initializing database schema...")
    init_db()


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class DiagnoseRequest(BaseModel):
    log_text: str
    repo_path: Optional[str] = None
    apply_fix: bool = True

def verify_github_signature(raw_body: bytes, signature_header: Optional[str]) -> bool:
    """
    Verifies that the webhook payload is signed with the correct secret.
    """
    secret = settings.github_webhook_secret
    if not secret:
        # If secret is not configured, signature verification is skipped (useful for local testing)
        logger.warning("GITHUB_WEBHOOK_SECRET is not configured. Skipping signature verification.")
        return True
        
    if not signature_header:
        logger.error("Signature header missing but webhook secret is configured.")
        return False
        
    if not signature_header.startswith("sha256="):
        logger.error("Invalid signature format. Expected sha256=...")
        return False
        
    expected_hash = signature_header.split("sha256=")[1]
    
    # Calculate signature
    import hmac
    import hashlib
    hasher = hmac.new(secret.encode("utf-8"), msg=raw_body, digestmod=hashlib.sha256)
    calculated_hash = hasher.hexdigest()
    
    return hmac.compare_digest(expected_hash, calculated_hash)

def process_failed_run(repo_name: str, run_id: int):
    """
    Background worker that fetches run logs, runs diagnostics, applies changes, and creates a PR.
    """
    logger.info(f"Starting self-healing process for run {run_id} in {repo_name}")
    
    # Create database session
    db = SessionLocal()
    
    # 1. Create or retrieve the run record in DB
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    run_record = db.query(HealingRunRecord).filter(HealingRunRecord.id == str(run_id)).first()
    if not run_record:
        run_record = HealingRunRecord(
            id=str(run_id),
            job_name="Workflow Failure Diagnosis",
            repo=repo_name,
            branch="master",  # default fallback
            timestamp=timestamp,
            status="diagnosing",
            explanation="Starting log diagnostics...",
            modifications=[]
        )
        db.add(run_record)
        db.commit()
        db.refresh(run_record)
        
    workspace_dir = os.path.join(settings.workspace_dir, f"run-{run_id}")
    try:
        # 1. Instantiate the GitHub client wrapper
        gh_client = GitHubClientWrapper()
        
        # 2. Fetch the run details and failed job logs
        run_details = gh_client.get_failed_run_details(repo_name, run_id)
        head_sha = run_details["head_sha"]
        base_branch = run_details["head_branch"]
        
        # Update run branch and job name from actual details
        run_record.branch = base_branch
        failed_jobs = [j["name"] for j in run_details.get("jobs", []) if j.get("conclusion") == "failure"]
        if failed_jobs:
            run_record.job_name = f"Job Failure: {', '.join(failed_jobs)}"
        db.commit()
        
        # Extract log text from failed jobs
        failed_logs = []
        for job in run_details.get("jobs", []):
            if job.get("conclusion") == "failure" and job.get("logs"):
                failed_logs.append(f"--- Job: {job['name']} ---\n{job['logs']}")
        
        if not failed_logs:
            logger.warning(f"No failure logs found for run {run_id}.")
            run_record.status = "failed"
            run_record.explanation = "No failure logs found for this workflow run."
            db.commit()
            return
        
        log_text = "\n".join(failed_logs)
        
        # 3. Clone repository and checkout failed SHA locally
        if os.path.exists(workspace_dir):
            shutil.rmtree(workspace_dir, ignore_errors=True)
        os.makedirs(workspace_dir, exist_ok=True)
        
        clone_url = f"https://x-access-token:{gh_client.token}@github.com/{repo_name}.git"
        logger.info(f"Cloning {repo_name} to {workspace_dir} and checking out SHA {head_sha}")
        repo = git.Repo.clone_from(clone_url, workspace_dir)
        repo.git.checkout(head_sha)
        
        # 4. Run LLM diagnostics on the failed log and cloned codebase
        analysis = diagnose_and_repair(log_text, workspace_dir)
        explanation = analysis.get("explanation", "No explanation provided.")
        modifications = analysis.get("modifications", [])
        
        # Update explanation in DB
        run_record.explanation = explanation
        db.commit()
        
        if not modifications:
            logger.info(f"No modifications proposed by LLM for run {run_id}. Explanation: {explanation}")
            run_record.status = "failed"
            run_record.explanation = f"LLM diagnosed the issue but proposed no code modifications. Diagnosis: {explanation}"
            db.commit()
            return
            
        # 5. Transition to 'healing' state
        run_record.status = "healing"
        run_record.explanation = f"Proposed changes: {len(modifications)} modifications. Applying fixes and staging..."
        db.commit()
        
        # 6. Create a new fix branch from the failed SHA
        branch_name = f"fix/failed-run-{run_id}"
        logger.info(f"Creating branch {branch_name} from SHA {head_sha}")
        new_branch = repo.create_head(branch_name)
        new_branch.checkout()
        
        # 7. Apply the modifications to the checkout files
        apply_results = apply_modifications(workspace_dir, modifications)
        
        if not any(apply_results.values()):
            logger.error("Failed to apply any of the LLM modifications.")
            run_record.status = "failed"
            run_record.explanation = f"Failed to apply proposed code modifications: {modifications}"
            db.commit()
            return
            
        # 8. Add, commit, and push the branch
        repo.git.add(A=True)
        if not repo.is_dirty():
            logger.warning("No changes detected after applying modifications. Skipping push.")
            run_record.status = "failed"
            run_record.explanation = "No modifications could be applied or repo not dirty."
            db.commit()
            return
            
        with repo.config_writer() as cw:
            cw.set_value("user", "name", "SRE Self-Healing Agent")
            cw.set_value("user", "email", "sre-agent-bot@users.noreply.github.com")
        
        commit_message = f"fix: resolve CI failure in run {run_id}\n\nLLM Diagnosis:\n{explanation}"
        repo.index.commit(commit_message)
        
        logger.info(f"Pushing branch {branch_name} to remote")
        repo.git.push("origin", branch_name, force=True)
        
        # 9. Create or update a Pull Request on GitHub
        pr_title = f"fix(ci): Resolve CI/CD pipeline failure on run #{run_id}"
        pr_body = (
            f"### 🤖 SRE Self-Healing Agent Report\n\n"
            f"The CI/CD pipeline failed on run #{run_id} on branch `{base_branch}`.\n\n"
            f"#### 🔍 Root Cause Analysis:\n"
            f"{explanation}\n\n"
            f"#### 🛠 Proposed Changes:\n"
            + "\n".join([f"- Modify `{mod.get('filepath')}` ({mod.get('action')})" for mod in modifications])
        )
        
        pygithub_repo = gh_client.g.get_repo(repo_name)
        owner = pygithub_repo.owner.login
        pulls = pygithub_repo.get_pulls(
            state="open",
            head=f"{owner}:{branch_name}",
            base=base_branch
        )
        
        if pulls.totalCount > 0:
            pr = pulls[0]
            logger.info(f"PR already exists, updating: {pr.html_url}")
            pr.edit(title=pr_title, body=pr_body)
            pr_url = pr.html_url
        else:
            logger.info(f"Creating new PR to {base_branch}")
            pr = pygithub_repo.create_pull(
                title=pr_title,
                body=pr_body,
                head=branch_name,
                base=base_branch
            )
            pr_url = pr.html_url
            
        # 10. Update DB to 'resolved' with details
        run_record.status = "resolved"
        run_record.explanation = explanation
        run_record.pr_url = pr_url
        run_record.modifications = modifications
        db.commit()
        logger.info(f"Self-healing PR successfully created/updated: {pr_url}")
        
    except Exception as e:
        logger.error(f"Error in background self-healing process: {e}", exc_info=True)
        run_record.status = "failed"
        run_record.explanation = f"Error occurred during background repair: {str(e)}"
        db.commit()
    finally:
        db.close()
        if os.path.exists(workspace_dir):
            shutil.rmtree(workspace_dir, ignore_errors=True)

@app.get("/health")
async def health_check():
    """
    Health check endpoint.
    """
    return {"status": "healthy", "github_token_configured": bool(settings.github_token)}

@app.post("/diagnose")
async def diagnose(request: DiagnoseRequest):
    repo_path = request.repo_path or settings.workspace_dir
    os.makedirs(repo_path, exist_ok=True)
    
    logger.info(f"Diagnose request received for repo: {repo_path}")
    try:
        result = diagnose_and_repair(request.log_text, repo_path)
        if request.apply_fix and result.get("modifications"):
            apply_results = apply_modifications(repo_path, result["modifications"])
            result["apply_results"] = apply_results
        return result
    except Exception as e:
        logger.error(f"Error during manual diagnostics: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/webhook")
async def webhook_receiver(
    request: Request,
    background_tasks: BackgroundTasks,
    x_github_event: Optional[str] = Header(None, alias="X-GitHub-Event"),
    x_hub_signature_256: Optional[str] = Header(None, alias="X-Hub-Signature-256")
):
    """
    GitHub webhook receiver for workflow_run.completed with a 'failure' conclusion.
    """
    logger.info("Received a new webhook request")
    
    # 1. Read request body and verify signature
    body_bytes = await request.body()
    if not verify_github_signature(body_bytes, x_hub_signature_256):
        logger.warning("Signature verification failed.")
        raise HTTPException(status_code=401, detail="Invalid signature")
        
    # 2. Extract JSON payload
    try:
        payload = json.loads(body_bytes)
    except Exception as e:
        logger.error(f"Failed to parse JSON body: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON body")
        
    # 3. Check event type
    if x_github_event == "ping":
        return {"message": "pong"}
        
    if x_github_event != "workflow_run":
        logger.info(f"Ignoring event type: {x_github_event}")
        return JSONResponse(status_code=200, content={"message": f"Ignored event type: {x_github_event}"})
        
    action = payload.get("action")
    workflow_run = payload.get("workflow_run", {})
    conclusion = workflow_run.get("conclusion")
    run_id = workflow_run.get("id")
    
    repo_name = (
        payload.get("repository", {}).get("full_name") or
        workflow_run.get("repository", {}).get("full_name")
    )
    
    logger.info(f"Event: {x_github_event}, Action: {action}, Conclusion: {conclusion}, Run ID: {run_id}, Repo: {repo_name}")
    
    # We only care when a workflow run is completed and has failed
    if action == "completed" and conclusion == "failure":
        if not repo_name or not run_id:
            logger.error("Missing repo_name or run_id in workflow_run payload.")
            raise HTTPException(status_code=422, detail="Missing repository name or run ID in payload")
            
        # Queue the processing in background tasks to avoid webhook timeout
        background_tasks.add_task(process_failed_run, repo_name, run_id)
        logger.info(f"Queued background processing for workflow run {run_id}")
        return JSONResponse(status_code=202, content={"message": f"Processing workflow run {run_id} failure in background."})
        
    logger.info("Webhook event ignored (not a completed failure run)")
    return JSONResponse(status_code=200, content={"message": "Webhook received, no action required."})

def get_current_user(authorization: Optional[str] = Header(None), db: Session = Depends(get_db)) -> User:
    """Dependency to retrieve the currently logged in user based on the Authorization Bearer header."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication token missing or invalid")
    token = authorization.split(" ")[1]
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        user_id = payload.get("user_id")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token payload")
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        return user
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Token verification failed")

@app.get("/auth/login")
async def auth_login():
    """Returns the GitHub authorization redirect URL."""
    github_url = (
        f"https://github.com/login/oauth/authorize"
        f"?client_id={settings.github_client_id}"
        f"&scope=read:user,repo"
    )
    return {"url": github_url}

@app.get("/auth/callback")
async def auth_callback(code: str, db: Session = Depends(get_db)):
    """Handles GitHub OAuth redirection, exchanges code for access token, and issues JWT session token."""
    async with httpx.AsyncClient() as client:
        # Exchange code for access token
        res = await client.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": settings.github_client_id,
                "client_secret": settings.github_client_secret,
                "code": code,
            }
        )
        if res.status_code != 200:
            raise HTTPException(status_code=400, detail="Failed to retrieve access token from GitHub")
        data = res.json()
        access_token = data.get("access_token")
        if not access_token:
            raise HTTPException(status_code=400, detail=f"No access token returned: {data}")

        # Fetch user details from GitHub
        user_res = await client.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json"
            }
        )
        if user_res.status_code != 200:
            raise HTTPException(status_code=400, detail="Failed to fetch user profile from GitHub")
        
        user_data = user_res.json()
        github_id = user_data.get("id")
        username = user_data.get("login")
        name = user_data.get("name")
        avatar_url = user_data.get("avatar_url")

        # Create or update user in database
        user = db.query(User).filter(User.github_id == github_id).first()
        if not user:
            user = User(
                github_id=github_id,
                username=username,
                name=name,
                avatar_url=avatar_url,
                access_token=access_token
            )
            db.add(user)
        else:
            user.username = username
            user.name = name
            user.avatar_url = avatar_url
            user.access_token = access_token
        db.commit()
        db.refresh(user)

        # Generate JWT session token
        session_token = jwt.encode(
            {"user_id": user.id, "username": user.username},
            settings.jwt_secret,
            algorithm="HS256"
        )

        # Redirect the user back to the React frontend with the token in query parameter
        redirect_url = f"https://pipeline-agent.tech/?token={session_token}"
        return RedirectResponse(url=redirect_url)

@app.get("/auth/me")
async def auth_me(current_user: User = Depends(get_current_user)):
    """Fetch profile of current logged in user."""
    return {
        "username": current_user.username,
        "name": current_user.name or current_user.username,
        "avatar_url": current_user.avatar_url,
    }

@app.get("/repos")
async def get_user_repos(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Fetch user's repositories from GitHub and merge with their DB status."""
    async with httpx.AsyncClient() as client:
        github_res = await client.get(
            "https://api.github.com/user/repos?type=owner&per_page=100",
            headers={"Authorization": f"Bearer {current_user.access_token}"}
        )
        if github_res.status_code != 200:
            raise HTTPException(status_code=github_res.status_code, detail="Failed to fetch repositories from GitHub")
        
        repos_data = github_res.json()
        result = []
        for r in repos_data:
            full_name = r.get("full_name")
            github_id = r.get("id")
            default_branch = r.get("default_branch", "master")
            
            # Check if this repo is in our DB
            db_repo = db.query(MonitoredRepo).filter(MonitoredRepo.github_id == github_id).first()
            if not db_repo:
                db_repo = MonitoredRepo(
                    github_id=github_id,
                    name=full_name,
                    branch=default_branch,
                    webhook_connected=True,
                    healing_enabled=True,
                    user_id=current_user.id
                )
                db.add(db_repo)
                db.commit()
                db.refresh(db_repo)
            
            result.append({
                "id": db_repo.id,
                "github_id": db_repo.github_id,
                "name": db_repo.name,
                "branch": db_repo.branch,
                "webhookConnected": db_repo.webhook_connected,
                "healingEnabled": db_repo.healing_enabled
            })
        return result

@app.post("/repos/{repo_id}/toggle-healing")
async def toggle_healing(repo_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Toggles auto-healing status for a repository."""
    db_repo = db.query(MonitoredRepo).filter(MonitoredRepo.id == repo_id, MonitoredRepo.user_id == current_user.id).first()
    if not db_repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    
    db_repo.healing_enabled = not db_repo.healing_enabled
    db.commit()
    db.refresh(db_repo)
    return {
        "id": db_repo.id,
        "name": db_repo.name,
        "healingEnabled": db_repo.healing_enabled
    }

@app.post("/repos/{repo_id}/simulate-failure")
async def simulate_failure(repo_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Initiates a database-backed background run simulation."""
    db_repo = db.query(MonitoredRepo).filter(MonitoredRepo.id == repo_id, MonitoredRepo.user_id == current_user.id).first()
    if not db_repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    
    import random
    run_id = f"sim-{random.randint(1000000000, 9999999999)}"
    job_id = f"job-{random.randint(1000000000, 9999999999)}"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    run_record = HealingRunRecord(
        id=run_id,
        job_id=job_id,
        job_name="Terraform Init and Plan",
        repo=db_repo.name,
        branch=db_repo.branch,
        timestamp=timestamp,
        status="diagnosing",
        explanation="SRE agent is currently downloading failed GitHub Action logs and analyzing the root cause...",
        modifications=[],
        user_id=current_user.id
    )
    db.add(run_record)
    db.commit()
    
    async def run_simulation():
        import asyncio
        await asyncio.sleep(3)
        db = SessionLocal()
        try:
            r = db.query(HealingRunRecord).filter(HealingRunRecord.id == run_id).first()
            if r:
                r.status = "healing"
                r.explanation = (
                    "LLM diagnosed a missing input variable declaration: \"bucket_name\". "
                    f"The agent is checking out a new branch \"fix/failed-run-{run_id}\", "
                    "writing the variable definition block, and staging changes..."
                )
                db.commit()
        finally:
            db.close()
            
        await asyncio.sleep(3)
        db = SessionLocal()
        try:
            r = db.query(HealingRunRecord).filter(HealingRunRecord.id == run_id).first()
            if r:
                r.status = "resolved"
                r.explanation = (
                    "Root cause identified: main.tf referenced var.bucket_name without an HCL variable declaration block. "
                    "SRE Agent declared variable \"bucket_name\" with a default value. Applied patch, pushed fix to branch, "
                    "and successfully created a Pull Request."
                )
                r.modifications = [{
                    "filepath": "main.tf",
                    "action": "write",
                    "content": (
                        "@@ -20,9 +20,13 @@\n"
                        " resource \"aws_s3_bucket\" \"demo_bucket\" {\n"
                        "-  bucket = var.bucket_name\n"
                        "+  bucket = var.bucket_name\n"
                        " }\n"
                        "+\n"
                        "+variable \"bucket_name\" {\n"
                        "+  type    = string\n"
                        "+  default = \"demo-s3-bucket-simulated\"\n"
                        "+}"
                    )
                }]
                r.pr_url = f"https://github.com/{db_repo.name}/pull/{random.randint(1, 100)}"
                db.commit()
        finally:
            db.close()
            
    import asyncio
    asyncio.create_task(run_simulation())
    
    return {"message": "Simulation started", "runId": run_id}

@app.get("/history")
async def get_healing_history(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Get the auto-healing logs from the database for repositories owned by the current user."""
    user_repos = db.query(MonitoredRepo).filter(MonitoredRepo.user_id == current_user.id).all()
    repo_names = [r.name for r in user_repos]
    
    runs = db.query(HealingRunRecord).filter(HealingRunRecord.repo.in_(repo_names)).order_by(HealingRunRecord.created_at.desc()).all()
    result = []
    for r in runs:
        result.append({
            "runId": r.id,
            "jobId": r.job_id,
            "jobName": r.job_name,
            "repo": r.repo,
            "branch": r.branch,
            "timestamp": r.timestamp,
            "status": r.status,
            "explanation": r.explanation,
            "modifications": r.modifications or [],
            "prUrl": r.pr_url
        })
    return result

if __name__ == "__main__":
    import uvicorn
    host = getattr(settings, "host", "0.0.0.0")
    port = getattr(settings, "port", 8000)
    uvicorn.run("main:app", host=host, port=port, reload=False)
