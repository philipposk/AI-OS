# Aider Integration - Code editing hands
import subprocess
import os

class AiderIntegration:
    def __init__(self, repo_path: str = "."):
        self.repo_path = repo_path
        
    def edit_file(self, file_path: str, changes: str):
        """Edit file using aider"""
        cmd = f"aider --file {file_path} --message '{changes}'"
        result = subprocess.run(cmd, shell=True, cwd=self.repo_path)
        return result.returncode == 0
    
    def commit_changes(self, message: str):
        """Commit changes with git"""
        subprocess.run(f"git add .", shell=True, cwd=self.repo_path)
        subprocess.run(f"git commit -m '{message}'", shell=True, cwd=self.repo_path)
        return True
    
    def run_tests(self, test_command: str = "pytest"):
        """Run tests"""
        result = subprocess.run(test_command, shell=True, cwd=self.repo_path)
        return result.returncode == 0

if __name__ == "__main__":
    print("Aider integration module loaded")