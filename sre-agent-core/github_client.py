import os
import shutil
import logging
import requests
from typing import Dict, Any, List, Optional
import git
from github import Github, GithubException
from config import settings

logger = logging.getLogger("sre-agent-core.github_client")

class GitHubClientWrapper:
    def __init__(self, token: str = None):
        self.token = token or settings.github_token
        if not self.token:
            raise ValueError("GitHub token must be provided or configured in GITHUB_TOKEN environment variable.")
        self.g = Github(self.token)

    def get_failed_run_details(self, repo_name: str, run_id: int) -> Dict[str, Any]:
        """
        Fetches details of the failed workflow run, including jobs and their logs.
        """
        logger.info(f"Fetching workflow run {run_id} for repo {repo_name}")
        try:
            repo = self.g.get_repo(repo_name)
            run = repo.get_workflow_run(run_id)
            
            jobs_data = []
            jobs = run.jobs()
            
            for job in jobs:
                job_info = {
                    "id": job.id,
                    "name": job.name,
                    "status": job.status,
                    "conclusion": job.conclusion,
                    "steps": [
                        {
                            "name": step.name,
                            "status": step.status,
                            "conclusion": step.conclusion,
                            "number": step.number
                        }
                        for step in job.steps
                    ],
                    "logs": ""
                }
                
                # Fetch logs only for failed jobs to save time and bandwidth
                if job.conclusion == "failure":
                    logger.info(f"Fetching logs for failed job: {job.name} (ID: {job.id})")
                    try:
                        # job.logs_url is a bound method in PyGithub, so we must call it
                        url = job.logs_url() if callable(job.logs_url) else job.logs_url
                        job_info["logs"] = self.fetch_job_logs(url)
                    except Exception as e:
                        logger.error(f"Error fetching logs for job {job.name}: {e}")
                        job_info["logs"] = f"Failed to download logs: {str(e)}"
                
                jobs_data.append(job_info)
                
            return {
                "run_id": run.id,
                "run_number": run.run_number,
                "event": run.event,
                "head_branch": run.head_branch,
                "head_sha": run.head_sha,
                "repository": repo_name,
                "jobs": jobs_data
            }
        except GithubException as e:
            logger.error(f"GitHub API Error: {e.data}")
            raise e

    def fetch_job_logs(self, logs_url: str) -> str:
        """
        Downloads logs from GitHub API, handling redirect safely without forwarding Auth headers to third-party CDNs.
        """
        # If it is already a redirect/blob storage URL (not on github), fetch it directly without headers.
        # Sending GitHub auth headers to Azure/S3 storage results in a 403 SAS signature rejection.
        if "github.com" not in logs_url and "api.github.com" not in logs_url:
            logger.info("Logs URL is hosted externally. Fetching directly without authorization header.")
            response = requests.get(logs_url)
            if response.status_code == 200:
                return response.text
            raise IOError(f"Failed to fetch logs from external storage. Status code: {response.status_code}")

        headers = {
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github+json"
        }
        
        # Don't follow redirects automatically to avoid auth-header leakage to storage CDNs (e.g. S3)
        response = requests.get(logs_url, headers=headers, allow_redirects=False)
        
        if response.status_code in (301, 302, 307, 308):
            redirect_url = response.headers.get("Location")
            if redirect_url:
                logger.info(f"Redirecting log request to: {redirect_url[:60]}...")
                # Request the actual logs file without authentication header
                redirect_response = requests.get(redirect_url)
                if redirect_response.status_code == 200:
                    return redirect_response.text
                else:
                    raise IOError(f"Failed to fetch logs from redirect. Status code: {redirect_response.status_code}")
        elif response.status_code == 200:
            return response.text
            
        raise IOError(f"Failed to fetch logs from {logs_url}. Status code: {response.status_code}")

    def clone_apply_and_pr(
        self,
        repo_name: str,
        run_id: int,
        head_sha: str,
        base_branch: str,
        files_to_modify: Dict[str, str],
        commit_message: str,
        pr_title: str,
        pr_body: str
    ) -> str:
        """
        Clones the repo, branches from the failed commit, applies modifications, commits, pushes, and creates/updates a PR.
        """
        workspace_dir = os.path.join(settings.workspace_dir, str(run_id))
        if os.path.exists(workspace_dir):
            shutil.rmtree(workspace_dir, ignore_errors=True)
            
        os.makedirs(workspace_dir, exist_ok=True)
        
        # Use oauth2 token format for authenticating git clone/push
        clone_url = f"https://x-access-token:{self.token}@github.com/{repo_name}.git"
        branch_name = f"fix/failed-run-{run_id}"
        
        try:
            logger.info(f"Cloning {repo_name} to {workspace_dir}")
            git_repo = git.Repo.clone_from(clone_url, workspace_dir)
            
            # Checkout the specific SHA that failed to base our fix on the correct state
            logger.info(f"Checking out head SHA {head_sha}")
            git_repo.git.checkout(head_sha)
            
            # Create and checkout new branch
            logger.info(f"Creating branch {branch_name}")
            new_branch = git_repo.create_head(branch_name)
            new_branch.checkout()
            
            # Apply file modifications
            for rel_path, content in files_to_modify.items():
                full_path = os.path.join(workspace_dir, rel_path)
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(content)
                logger.info(f"Modified file: {rel_path}")
                git_repo.index.add([rel_path])
                
            if not git_repo.is_dirty():
                logger.warning("No changes detected after applying modifications. Skipping commit/push.")
                return ""
                
            # Commit
            logger.info(f"Committing changes with message: {commit_message}")
            # Set git identity if not configured globally
            with git_repo.config_writer() as cw:
                cw.set_value("user", "name", "SRE Self-Healing Agent")
                cw.set_value("user", "email", "sre-agent@example.com")
            
            git_repo.index.commit(commit_message)
            
            # Push
            logger.info(f"Pushing branch {branch_name} to remote")
            git_repo.git.push("origin", branch_name, force=True)
            
            # Create or update PR
            pygithub_repo = self.g.get_repo(repo_name)
            
            # Check for existing PR
            logger.info("Checking for existing PR")
            owner = pygithub_repo.owner.login
            pulls = pygithub_repo.get_pulls(
                state="open",
                head=f"{owner}:{branch_name}",
                base=base_branch
            )
            
            if pulls.totalCount > 0:
                pr = pulls[0]
                logger.info(f"PR already exists, updating body: {pr.html_url}")
                pr.edit(title=pr_title, body=pr_body)
                return pr.html_url
            else:
                logger.info(f"Creating new PR to {base_branch}")
                pr = pygithub_repo.create_pull(
                    title=pr_title,
                    body=pr_body,
                    head=branch_name,
                    base=base_branch
                )
                return pr.html_url
                
        except Exception as e:
            logger.error(f"Error in clone_apply_and_pr: {e}")
            raise e
        finally:
            # Clean up cloned files to free up disk space
            shutil.rmtree(workspace_dir, ignore_errors=True)
            logger.info(f"Cleaned up workspace directory {workspace_dir}")
