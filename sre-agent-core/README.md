# SRE Self-Healing GitHub Actions Receiver

This is the core FastAPI backend that listens for GitHub Actions webhook failure events, downloads run logs, runs diagnostic analysis on the failures, and pushes automated bug fixes as Pull Requests.

## Directory Structure

```
sre-agent-core/
├── config.py           # Configuration module using pydantic-settings
├── github_client.py    # GitHub API client (PyGithub + GitPython) for log fetching and git operations
├── diagnostics.py     # Placeholder/Stub for the LLM Diagnostics Agent (Subagent 2)
├── main.py             # FastAPI webhook receiver and background task coordinator
├── requirements.txt    # Python dependencies
└── tests/
    └── test_webhook.py # Unit and integration tests
```

## Setup & Running

1. **Virtual Environment & Dependencies** (Already set up):
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Environment Variables**:
   Create a `.env` file inside the `sre-agent-core` directory or set these in your shell:
   ```env
   GITHUB_TOKEN=your_github_personal_access_token
   GITHUB_WEBHOOK_SECRET=your_github_webhook_signing_secret
   GEMINI_API_KEY=your_gemini_api_key
   PORT=8000
   HOST=0.0.0.0
   ```

3. **Run the server**:
   ```bash
   # Make sure PYTHONPATH is set to the sre-agent-core directory so imports work correctly
   PYTHONPATH=. venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --reload
   ```

4. **Run Tests**:
   ```bash
   PYTHONPATH=. venv/bin/pytest tests/
   ```

## Endpoint Details

- `GET /health`: Checks application health and reports if the GitHub token is configured.
- `POST /webhook`: Webhook receiver endpoint. Expects headers `X-GitHub-Event: workflow_run` and optionally `X-Hub-Signature-256`. It filters for:
  - `action == "completed"`
  - `conclusion == "failure"`
  
  When triggered, it launches an asynchronous task to process the failure, preventing webhook timeouts.

## Flow & Architecture

1. **Webhook Event Received**: `main.py` validates signature and event details.
2. **Background Process Dispatched**: `main.process_failed_run` is started in the background.
3. **Log Retrieval**: `github_client.py` uses PyGithub to fetch run job details and downloads failed job logs from the API (safely handling external storage redirects).
4. **Diagnostics**: `diagnostics.py` (to be fully implemented by Subagent 2) processes the logs and returns file modifications.
5. **Git Operations**: `github_client.py` clones the repo, checks out the failed commit SHA, applies modifications, commits, forces-pushes a new branch `fix/failed-run-<run_id>`, and opens a Pull Request back to the target repository's head branch.
