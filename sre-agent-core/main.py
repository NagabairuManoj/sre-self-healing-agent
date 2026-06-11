import os
import json
import logging
import shutil
import tempfile
import requests
import git
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

app = FastAPI(
    title="SRE Self-Healing Agent Core",
    description="FastAPI application receiving GitHub workflow_run failure webhooks and triggering auto-healing workflows.",
    version="1.0.0"
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
    workspace_dir = os.path.join(settings.workspace_dir, f"run-{run_id}")
    try:
        # 1. Instantiate the GitHub client wrapper
        gh_client = GitHubClientWrapper()
        
        # 2. Fetch the run details and failed job logs
        run_details = gh_client.get_failed_run_details(repo_name, run_id)
        head_sha = run_details["head_sha"]
        base_branch = run_details["head_branch"]
        
        # Extract log text from failed jobs
        failed_logs = []
        for job in run_details.get("jobs", []):
            if job.get("conclusion") == "failure" and job.get("logs"):
                failed_logs.append(f"--- Job: {job['name']} ---\n{job['logs']}")
        
        if not failed_logs:
            logger.warning(f"No failure logs found for run {run_id}.")
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
        
        if not modifications:
            logger.info(f"No modifications proposed by LLM for run {run_id}. Explanation: {explanation}")
            return
        
        # 5. Create a new fix branch from the failed SHA
        branch_name = f"fix/failed-run-{run_id}"
        logger.info(f"Creating branch {branch_name} from SHA {head_sha}")
        new_branch = repo.create_head(branch_name)
        new_branch.checkout()
        
        # 6. Apply the modifications to the checkout files
        apply_results = apply_modifications(workspace_dir, modifications)
        
        if not any(apply_results.values()):
            logger.error("Failed to apply any of the LLM modifications.")
            return
        
        # 7. Add, commit, and push the branch
        repo.git.add(A=True)
        if not repo.is_dirty():
            logger.warning("No changes detected after applying modifications. Skipping push.")
            return
            
        with repo.config_writer() as cw:
            cw.set_value("user", "name", "SRE Self-Healing Agent")
            cw.set_value("user", "email", "sre-agent-bot@users.noreply.github.com")
        
        commit_message = f"fix: resolve CI failure in run {run_id}\n\nLLM Diagnosis:\n{explanation}"
        repo.index.commit(commit_message)
        
        logger.info(f"Pushing branch {branch_name} to remote")
        repo.git.push("origin", branch_name, force=True)
        
        # 8. Create or update a Pull Request on GitHub
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
        
        logger.info(f"Self-healing PR successfully created/updated: {pr_url}")
        
    except Exception as e:
        logger.error(f"Error in background self-healing process: {e}", exc_info=True)
    finally:
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

if __name__ == "__main__":
    import uvicorn
    host = getattr(settings, "host", "0.0.0.0")
    port = getattr(settings, "port", 8000)
    uvicorn.run("main:app", host=host, port=port, reload=False)
