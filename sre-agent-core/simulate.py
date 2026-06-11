import os
import sys
import subprocess
import logging
from typing import Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("sre-agent-core.simulate")

try:
    from sre_agent_core.diagnostics import diagnose_and_repair, apply_modifications
except ImportError:
    from diagnostics import diagnose_and_repair, apply_modifications

def run_terraform_plan(cwd: str) -> tuple[int, str]:
    """Runs terraform plan and returns (exit_code, output)"""
    logger.info("Running terraform plan...")
    res = subprocess.run(["terraform", "plan"], cwd=cwd, capture_output=True, text=True)
    return res.returncode, res.stdout + "\n" + res.stderr

def simulate():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    demo_dir = os.path.join(base_dir, "demo-failing-infrastructure")
    
    if not os.path.exists(demo_dir):
        logger.error(f"Demo directory not found at {demo_dir}")
        sys.exit(1)
        
    logger.info("=== STEP 1: Running initial Terraform Plan (expected to fail) ===")
    exit_code, output = run_terraform_plan(demo_dir)
    logger.info(f"Initial Terraform plan finished with exit code {exit_code}")
    
    if exit_code == 0:
        logger.info("No failure detected in initial run! Infrastructure is already healthy.")
        return
        
    logger.info("=== STEP 2: Preprocessing Logs & Running LLM Diagnostics ===")
    
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logger.warning(
            "GEMINI_API_KEY environment variable is not set.\n"
            "To test with real Gemini API, run:\n"
            "  export GEMINI_API_KEY=your_key\n"
            "  PYTHONPATH=. venv/bin/python3 sre-agent-core/simulate.py\n\n"
            "We will simulate the Gemini response for mock testing purposes."
        )
        # Mock LLM Response for testing the pipeline locally without API key
        result = {
            "explanation": "The terraform plan failed because var.bucket_name was used in main.tf but never declared in a variables block. We will create a variables.tf file declaring the bucket_name variable.",
            "modifications": [
                {
                    "filepath": "variables.tf",
                    "action": "write",
                    "content": "variable \"bucket_name\" {\n  type        = string\n  description = \"The name of the S3 bucket\"\n  default     = \"sre-self-healing-demo-bucket\"\n}\n"
                }
            ]
        }
    else:
        try:
            result = diagnose_and_repair(output, demo_dir, api_key=api_key)
        except Exception as e:
            logger.error(f"LLM Diagnostics call failed: {e}")
            sys.exit(1)
            
    logger.info("=== LLM Diagnosis Summary ===")
    logger.info(result.get("explanation", "No explanation provided."))
    
    modifications = result.get("modifications", [])
    if not modifications:
        logger.warning("No modifications were suggested by the LLM.")
        return
        
    logger.info(f"=== STEP 3: Applying {len(modifications)} Suggestion(s) locally ===")
    apply_results = apply_modifications(demo_dir, modifications)
    
    for filepath, success in apply_results.items():
        status = "SUCCESS" if success else "FAILED"
        logger.info(f"Applying fix to {filepath}: {status}")
        
    if not any(apply_results.values()):
        logger.error("No modifications were applied successfully.")
        sys.exit(1)
        
    logger.info("=== STEP 4: Verifying Self-Healing Success with Terraform Plan ===")
    exit_code, verify_output = run_terraform_plan(demo_dir)
    logger.info(f"Post-fix Terraform plan finished with exit code {exit_code}")
    
    if exit_code == 0:
        logger.info("SUCCESS: The self-healing agent repaired the repository successfully! Terraform plan now passes.")
        # Cleanup variables.tf if it was written so the repo remains clean for future runs
        var_tf_path = os.path.join(demo_dir, "variables.tf")
        if os.path.exists(var_tf_path):
            os.remove(var_tf_path)
            logger.info("Cleaned up variables.tf to reset demo directory.")
    else:
        logger.error(f"FAILURE: Post-fix Terraform plan still failed.\nOutput:\n{verify_output}")
        sys.exit(1)

if __name__ == "__main__":
    simulate()
