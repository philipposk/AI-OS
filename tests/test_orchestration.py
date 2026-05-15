import unittest
import os
import tempfile
from unittest.mock import patch, MagicMock
from orchestration import Orchestrator

class TestOrchestration(unittest.TestCase):
    
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        os.chdir(self.temp_dir)
        self.orch = Orchestrator()
    
    def tearDown(self):
        os.chdir('/')
        import shutil
        shutil.rmtree(self.temp_dir)
    
    @patch('orchestration.OpenRouterClient')
    def test_orchestrator_initialization(self, mock_client):
        """Test that orchestrator initializes correctly"""
        self.orch.client = mock_client.return_value
        self.assertIsNotNone(self.orch)
        self.assertEqual(self.orch.route_to_model("plan"), "openrouter/free")
    
    @patch('orchestration.OpenRouterClient')
    def test_plan_task_breakdown(self, mock_client):
        """Test task planning creates correct steps"""
        self.orch.client = mock_client.return_value
        steps = self.orch.plan_task("Write a Python function")
        
        self.assertEqual(len(steps), 5)
        
        step_names = [s["step"] for s in steps]
        expected = ["analyze", "plan", "code_generation", "test", "commit"]
        self.assertEqual(step_names, expected)
    
    @patch('orchestration.OpenRouterClient')
    def test_safe_api_call_success(self, mock_client):
        """Test successful API call"""
        self.orch.client = mock_client.return_value
        mock_client.return_value.chat.return_value = "Test response"
        
        result = self.orch.safe_api_call("test prompt")
        
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["response"], "Test response")
    
    @patch('orchestration.OpenRouterClient')
    def test_safe_api_call_timeout(self, mock_client):
        """Test API timeout handling"""
        self.orch.client = mock_client.return_value
        mock_client.return_value.chat.side_effect = requests.exceptions.Timeout()
        
        result = self.orch.safe_api_call("test prompt")
        
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"], "API timeout")
    
    @patch('orchestration.subprocess.run')
    def test_execute_test_step_success(self, mock_run):
        """Test successful test execution"""
        mock_run.return_value.returncode = 0
        
        step = {"step": "test", "action": "Run tests"}
        result = self.orch.execute_test_step(step)
        
        self.assertEqual(result["status"], "completed")
        self.assertIn("timestamp", result)
    
    @patch('orchestration.subprocess.run')
    def test_execute_test_step_failure(self, mock_run):
        """Test test execution failure"""
        mock_run.return_value.returncode = 1
        mock_run.return_value.stdout = "Test failed"
        
        step = {"step": "test", "action": "Run tests"}
        result = self.orch.execute_test_step(step)
        
        self.assertEqual(result["status"], "failed")
        self.assertIn("Test failed", result["output"])

if __name__ == '__main__':
    unittest.main()