import hmac
import hashlib
import pytest
from fastapi.testclient import TestClient

# We set the environment variable GITHUB_WEBHOOK_SECRET for consistent testing
import os
os.environ["GITHUB_WEBHOOK_SECRET"] = "testsecret"
os.environ["GITHUB_TOKEN"] = "testtoken"

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import app, verify_github_signature

client = TestClient(app)

def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
    assert response.json()["github_token_configured"] is True

def test_verify_signature_valid():
    secret = "testsecret"
    body = b"hello world"
    hasher = hmac.new(secret.encode("utf-8"), msg=body, digestmod=hashlib.sha256)
    signature = f"sha256={hasher.hexdigest()}"
    
    assert verify_github_signature(body, signature) is True

def test_verify_signature_invalid():
    body = b"hello world"
    signature = "sha256=invalidhash"
    assert verify_github_signature(body, signature) is False

def test_webhook_ignored_event():
    raw_body = b'{"action":"completed"}'
    # Compute valid signature
    hasher = hmac.new(b"testsecret", msg=raw_body, digestmod=hashlib.sha256)
    signature = f"sha256={hasher.hexdigest()}"
    
    headers = {
        "X-GitHub-Event": "issues",
        "X-Hub-Signature-256": signature
    }
    response = client.post("/webhook", content=raw_body, headers=headers)
    assert response.status_code == 200
    assert "Ignored event type: issues" in response.json()["message"]

def test_webhook_unauthorized():
    body = {"action": "completed"}
    headers = {
        "X-GitHub-Event": "workflow_run",
        "X-Hub-Signature-256": "sha256=wrongsignature"
    }
    response = client.post("/webhook", json=body, headers=headers)
    assert response.status_code == 401

def test_webhook_successful_trigger_queued(monkeypatch):
    body = {
        "action": "completed",
        "workflow_run": {
            "id": 98765,
            "conclusion": "failure",
            "head_branch": "main",
            "head_sha": "abcdef123456"
        },
        "repository": {
            "full_name": "test-owner/test-repo"
        }
    }
    
    # Sign body
    import json
    raw_body = json.dumps(body, separators=(',', ':')).encode("utf-8")
    hasher = hmac.new(b"testsecret", msg=raw_body, digestmod=hashlib.sha256)
    signature = f"sha256={hasher.hexdigest()}"
    
    headers = {
        "X-GitHub-Event": "workflow_run",
        "X-Hub-Signature-256": signature
    }
    
    # Mock the background process task so we don't make real GitHub API calls
    bg_task_called = False
    
    def mock_process_failed_run(repo_name, run_id):
        nonlocal bg_task_called
        bg_task_called = True
        assert repo_name == "test-owner/test-repo"
        assert run_id == 98765

    import main
    monkeypatch.setattr(main, "process_failed_run", mock_process_failed_run)
    
    response = client.post("/webhook", content=raw_body, headers=headers)
    assert response.status_code == 202
    assert "Processing workflow run 98765 failure in background" in response.json()["message"]
