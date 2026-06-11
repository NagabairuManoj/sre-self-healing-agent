import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

try:
    from sre_agent_core.diagnostics import (
        preprocess_log,
        collect_codebase_context,
        apply_modifications,
        diagnose_and_repair
    )
except ImportError:
    from diagnostics import (
        preprocess_log,
        collect_codebase_context,
        apply_modifications,
        diagnose_and_repair
    )

class TestDiagnostics(unittest.TestCase):
    
    def test_preprocess_log(self):
        # Create a large log
        log_lines = []
        for i in range(1000):
            if i == 500:
                log_lines.append("Error: Reference to undeclared input variable")
            elif i == 800:
                log_lines.append("panic: traceback stack trace")
            else:
                log_lines.append(f"Normal log line {i}")
                
        log_text = "\n".join(log_lines)
        processed = preprocess_log(log_text, max_lines=200)
        
        # Verify it filtered the log and kept key errors
        self.assertIn("Error: Reference to undeclared input variable", processed)
        self.assertIn("panic: traceback stack trace", processed)
        self.assertLess(len(processed.splitlines()), 1000)
        
    def test_collect_codebase_context(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create some test files
            with open(os.path.join(tmpdir, "main.tf"), "w") as f:
                f.write("resource \"aws_s3_bucket\" \"demo\" {}")
            
            # Create a nested file
            os.makedirs(os.path.join(tmpdir, "modules"), exist_ok=True)
            with open(os.path.join(tmpdir, "modules", "s3.tf"), "w") as f:
                f.write("variable \"bucket_name\" {}")
                
            # Create an ignored file
            with open(os.path.join(tmpdir, "unsupported.exe"), "w") as f:
                f.write("binary content")
                
            context = collect_codebase_context(tmpdir)
            
            self.assertIn("main.tf", context)
            self.assertIn("modules/s3.tf", context)
            self.assertNotIn("unsupported.exe", context)
            self.assertEqual(context["main.tf"], "resource \"aws_s3_bucket\" \"demo\" {}")
            
    def test_apply_modifications_write(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            modifications = [
                {
                    "filepath": "new_file.tf",
                    "action": "write",
                    "content": "variable \"test\" {}"
                }
            ]
            
            results = apply_modifications(tmpdir, modifications)
            self.assertTrue(results.get("new_file.tf"))
            
            new_file_path = os.path.join(tmpdir, "new_file.tf")
            self.assertTrue(os.path.exists(new_file_path))
            with open(new_file_path, "r") as f:
                self.assertEqual(f.read(), "variable \"test\" {}")

    @patch('requests.post')
    def test_diagnose_and_repair_success(self, mock_post):
        # Mock Gemini API response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": '{"explanation": "Undeclared variable", "modifications": [{"filepath": "main.tf", "action": "write", "content": "variable bucket {}"}]}'
                            }
                        ]
                    }
                }
            ]
        }
        mock_post.return_value = mock_response
        
        with tempfile.TemporaryDirectory() as tmpdir:
            result = diagnose_and_repair(
                log_text="Error: Reference to undeclared input variable",
                repo_path=tmpdir,
                api_key="mock_key"
            )
            
            self.assertEqual(result["explanation"], "Undeclared variable")
            self.assertEqual(result["modifications"][0]["filepath"], "main.tf")
            self.assertEqual(result["modifications"][0]["content"], "variable bucket {}")

if __name__ == "__main__":
    unittest.main()
