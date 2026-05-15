# Model Router - Select which AI model to use for each task
MODELS = {
    "planning": "openai/claude-3-opus-20240229",
    "coding": "groq/llama-3.1-70b-versatile",
    "cheap": "ollama/qwen3:14b",
    "reasoning": "deepseek/deepseek-r1-distill-14b",
    "fast": "groq/llama-3.1-8b-instant"
}

# Model selection logic
def select_model(task_type: str) -> str:
    """Choose best model for task type"""
    return MODELS.get(task_type, MODELS["cheap"])

def get_all_models():
    """Return all available models"""
    return MODELS