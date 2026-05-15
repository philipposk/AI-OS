import os
import subprocess
import tempfile
from typing import List, Dict

# Aider integration - Code editing hands
class AiderIntegration:
    def __init__(self, repo_path: str = "."):
        self.repo_path = repo_path
        self._ensure_aider_installed()
    
    def _ensure_aider_installed(self):
        """Check if aider is installed, install if not"""
        try:
            subprocess.run(["aider", "--version"], capture_output=True, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            print("Installing aider...")
            subprocess.run(
                ["pip", "install", "aider"],
                capture_output=True
            )
    
    def edit_file(self, file_path: str, changes: str) -> bool:
        """Edit a file using aider"""
        try:
            result = subprocess.run(
                ["aider", "--file", file_path, "--message", changes],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=120
            )
            return result.returncode == 0
        except Exception as e:
            print(f"Aider error: {e}")
            return False
    
    def apply_prompt(self, prompt: str) -> bool:
        """Apply a general prompt to edit the repository"""
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
                f.write(prompt)
                temp_file = f.name
            
            result = subprocess.run(
                ["aider", "--message-file", temp_file],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=300
            )
            
            os.unlink(temp_file)
            return result.returncode == 0
        except Exception as e:
            print(f"Aider error: {e}")
            return False
    
    def run_tests(self, test_command: str = "pytest") -> Dict:
        """Run tests and return results"""
        try:
            result = subprocess.run(
                test_command.split(),
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=120
            )
            return {
                "returncode": result.returncode,
                "stdout": result.stdout[-2000:],
                "stderr": result.stderr[-2000:]
            }
        except Exception as e:
            return {
                "returncode": -1,
                "error": str(e)
            }
    
    def commit_changes(self, message: str = "AI-generated changes") -> bool:
        """Commit changes to git"""
        try:
            subprocess.run(
                ["git", "add", "."],
                cwd=self.repo_path,
                check=True
            )
            subprocess.run(
                ["git", "commit", "-m", message],
                cwd=self.repo_path,
                check=True,
                capture_output=True
            )
            return True
        except Exception as e:
            print(f"Git error: {e}")
            return False
    
    def get_file_status(self) -> List[str]:
        """Get list of modified/untracked files"""
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self.repo_path,
                capture_output=True,
                text=True
            )
            return [line.strip() for line in result.stdout.strip().split('\n') if line.strip()]
        except Exception as e:
            return []