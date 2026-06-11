import os
import re
import json
import logging
import subprocess
import requests
from typing import Optional, Dict, List, Any

# Configure Logging
logger = logging.getLogger("sre-agent-core.diagnostics")

try:
    from sre_agent_core.config import settings
except ImportError:
    try:
        from config import settings
    except ImportError:
        # Fallback settings class if not found
        class MockSettings:
            gemini_api_key = os.environ.get("GEMINI_API_KEY")
            workspace_dir = os.environ.get("WORKSPACE_DIR", "/tmp/sre-agent-workspace")
        settings = MockSettings()

def preprocess_log(log_text: str, max_lines: int = 500) -> str:
    """
    Filters and extracts key information from a large log file.
    Focuses on error messages, stack traces, and the end of the log where failures usually happen.
    """
    if not log_text:
        return ""
        
    lines = log_text.splitlines()
    total_lines = len(lines)
    if total_lines <= max_lines:
        return log_text
    
    important_indices = set()
    
    # Keywords indicating issues
    keywords = [
        "error", "fail", "exception", "traceback", "fatal", "critical", 
        "exit code", "exit status", "undeclared", "incorrect", "invalid"
    ]
    
    for i, line in enumerate(lines):
        line_lower = line.lower()
        if any(kw in line_lower for kw in keywords):
            # Include a window of context around the error
            start = max(0, i - 5)
            end = min(total_lines, i + 15)
            for j in range(start, end):
                important_indices.add(j)
                
    # Always include the last 150 lines of the log
    last_lines_count = min(150, total_lines)
    for j in range(total_lines - last_lines_count, total_lines):
        important_indices.add(j)
        
    # Reconstruct the log with markers for skipped lines
    sorted_indices = sorted(list(important_indices))
    result = []
    last_idx = -1
    
    for idx in sorted_indices:
        if last_idx != -1 and idx > last_idx + 1:
            result.append(f"\n... [skipped {idx - last_idx - 1} lines] ...\n")
        result.append(lines[idx])
        last_idx = idx
        
    return "\n".join(result)

def collect_codebase_context(repo_path: str) -> Dict[str, str]:
    """
    Recursively finds text files in the repository and reads their contents.
    Ignores common binary/ignored paths like .git, venv, node_modules.
    """
    context = {}
    ignored_dirs = {".git", "venv", ".venv", "node_modules", "__pycache__", ".terraform", ".github"}
    allowed_extensions = {
        ".tf", ".tfvars", ".py", ".yml", ".yaml", ".sh", ".txt", ".json", 
        ".md", ".cfg", ".ini", ".conf", ".go", ".js", ".ts"
    }
    
    if not os.path.exists(repo_path):
        logger.warning(f"Repository path {repo_path} does not exist.")
        return context

    for root, dirs, files in os.walk(repo_path):
        # Modifying dirs in-place to prune ignored directories
        dirs[:] = [d for d in dirs if d not in ignored_dirs and not d.startswith(".")]
        
        for file in files:
            if file.startswith("."):
                continue
            ext = os.path.splitext(file)[1].lower()
            if ext in allowed_extensions or file in ("Dockerfile", "Makefile"):
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, repo_path)
                try:
                    with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                        context[rel_path] = f.read()
                except Exception as e:
                    logger.warning(f"Failed to read file {rel_path} for context: {e}")
                    
    return context

def call_gemini_api(prompt: str, system_instruction: str = None, api_key: str = None) -> str:
    """
    Calls the Gemini API using HTTP POST request.
    Defaults to gemini-2.5-flash with a fallback to gemini-1.5-flash.
    """
    if not api_key:
        api_key = getattr(settings, "gemini_api_key", None) or os.environ.get("GEMINI_API_KEY")
        
    if not api_key:
        raise ValueError("GEMINI_API_KEY is not configured or set in the environment.")
        
    models = ["gemini-2.5-flash", "gemini-1.5-flash"]
    last_error = None
    
    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        headers = {"Content-Type": "application/json"}
        
        contents = {
            "contents": [{
                "parts": [{"text": prompt}]
            }],
            "generationConfig": {
                "responseMimeType": "application/json",
            }
        }
        
        if system_instruction:
            contents["systemInstruction"] = {
                "parts": [{"text": system_instruction}]
            }
            
        try:
            logger.info(f"Sending request to Gemini API using model {model}...")
            response = requests.post(url, headers=headers, json=contents, timeout=60)
            if response.status_code == 200:
                resp_json = response.json()
                try:
                    text = resp_json['candidates'][0]['content']['parts'][0]['text']
                    logger.info("Successfully received response from Gemini API.")
                    return text
                except (KeyError, IndexError) as parse_err:
                    last_error = f"Failed to parse response structure: {parse_err}. Response: {resp_json}"
                    logger.warning(last_error)
            else:
                last_error = f"API returned status {response.status_code}: {response.text}"
                logger.warning(last_error)
        except Exception as e:
            last_error = str(e)
            logger.warning(f"Exception during call to model {model}: {e}")
            
    raise RuntimeError(f"Failed to query Gemini API. Last error: {last_error}")

def diagnose_and_repair(log_text: str, repo_path: str, api_key: str = None, custom_instructions: str = None) -> Dict[str, Any]:
    """
    Diagnoses build/test logs and suggests code modifications to fix the errors.
    Returns a dictionary containing explanation and a list of modifications.
    """
    logger.info("Starting log diagnostics and repair analysis...")
    preprocessed_log = preprocess_log(log_text)
    codebase = collect_codebase_context(repo_path)
    
    codebase_str = ""
    for filepath, content in codebase.items():
        codebase_str += f"\n--- File: {filepath} ---\n{content}\n"
        
    system_instruction = (
        "You are an expert SRE and software developer. Your task is to diagnose the provided build/test log failure, "
        "identify the root cause in the provided codebase context, and generate the necessary file modifications to fix it. "
        "You MUST return a JSON object with two fields:\n"
        "1. 'explanation': A string diagnosing the failure and explaining the fix.\n"
        "2. 'modifications': An array of modification objects. Each modification object MUST have:\n"
        "   - 'filepath': string path of the file relative to repository root.\n"
        "   - 'action': 'write' (to overwrite/create file with complete contents) or 'patch' (to apply a unified diff/patch).\n"
        "   - 'content': string containing full content for 'write', or diff text for 'patch'.\n\n"
        "Ensure the JSON matches the schema and is valid. Use 'write' action with full contents for config files, "
        "variables files, or small scripts, as it is much cleaner and less error-prone than applying a patch."
    )
    if custom_instructions:
        system_instruction += f"\n\nAdditional SRE Instructions specific to this repository:\n{custom_instructions}"

    
    prompt = (
        f"--- FAILING LOG OUTPUT ---\n{preprocessed_log}\n\n"
        f"--- CODEBASE CONTEXT ---\n{codebase_str}\n\n"
        "Analyze the error, locate the files that need to be changed, and generate the modifications. Return ONLY the JSON object."
    )
    
    response_text = call_gemini_api(prompt, system_instruction=system_instruction, api_key=api_key)
    
    response_text = response_text.strip()
    if response_text.startswith("```"):
        lines = response_text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        response_text = "\n".join(lines).strip()
        
    try:
        result = json.loads(response_text)
        return result
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Gemini response as JSON: {e}\nResponse: {response_text}")
        match = re.search(r"\{.*\}", response_text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                pass
        raise ValueError(f"Gemini response was not valid JSON: {response_text}")

def apply_modifications(repo_path: str, modifications: List[Dict[str, str]]) -> Dict[str, bool]:
    """
    Applies the list of modifications to the codebase locally.
    Returns a dictionary of filepath -> success_boolean.
    """
    results = {}
    for mod in modifications:
        filepath = mod.get("filepath")
        action = mod.get("action")
        content = mod.get("content", "")
        
        if not filepath or not action:
            logger.warning(f"Skipping invalid modification object: {mod}")
            continue
            
        full_path = os.path.abspath(os.path.join(repo_path, filepath))
        # Basic security path traversal check
        if not full_path.startswith(os.path.abspath(repo_path)):
            logger.error(f"Security Warning: Attempted path traversal via file path {filepath}")
            results[filepath] = False
            continue
            
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        
        if action == "write":
            try:
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(content)
                logger.info(f"Successfully wrote file: {filepath}")
                results[filepath] = True
            except Exception as e:
                logger.error(f"Failed to write file {filepath}: {e}")
                results[filepath] = False
                
        elif action == "patch":
            patch_file = os.path.join(repo_path, f"temp_{os.path.basename(filepath)}.patch")
            try:
                with open(patch_file, "w", encoding="utf-8") as f:
                    f.write(content)
                
                # Try patch -t -f -p1
                cmd = ["patch", "-t", "-f", "-p1", "-i", os.path.relpath(patch_file, repo_path)]
                res = subprocess.run(cmd, cwd=repo_path, capture_output=True, text=True)
                
                if res.returncode != 0:
                    # Try patch -t -f -p0
                    cmd = ["patch", "-t", "-f", "-p0", "-i", os.path.relpath(patch_file, repo_path)]
                    res = subprocess.run(cmd, cwd=repo_path, capture_output=True, text=True)
                    
                if res.returncode == 0:
                    logger.info(f"Successfully patched file: {filepath}")
                    results[filepath] = True
                else:
                    logger.error(f"Failed to apply patch to {filepath}: {res.stderr}")
                    results[filepath] = False
            except Exception as e:
                logger.error(f"Exception while applying patch to {filepath}: {e}")
                results[filepath] = False
            finally:
                if os.path.exists(patch_file):
                    os.remove(patch_file)
        else:
            logger.warning(f"Unknown modification action '{action}' for file {filepath}")
            results[filepath] = False
            
    return results
