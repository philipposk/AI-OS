import os
import json
import logging
import requests
from typing import List, Dict

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class OpenRouterClient:
    def __init__(self):
        self.api_key = os.getenv("OPENROUTER_API_KEY")
        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY environment variable is required")

        self.base_url = "https://openrouter.ai/api/v1/chat/completions"

    def _make_request(self, model: str, messages: List[Dict]) -> Dict:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": 1000,
            "temperature": 0.7
        }

        try:
            response = requests.post(
                self.base_url,
                headers=headers,
                json=payload,
                timeout=30
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"OpenRouter request failed: {e}")
            raise
        except json.JSONDecodeError:
            logger.error("Invalid JSON response from OpenRouter")
            raise

    def chat(self, messages: List[Dict], model: str = "openrouter/free") -> str:
        try:
            response = self._make_request(model, messages)
            choices = response.get("choices", [])
            if not choices:
                raise ValueError("No choices in OpenRouter response")
            return choices[0]["message"]["content"]
        except Exception as e:
            logger.error(f"Error in OpenRouter chat: {e}")
            raise

if __name__ == "__main__":
    client = OpenRouterClient()
    messages = [{"role": "user", "content": "Hello! How are you?"}]
    try:
        response = client.chat(messages)
        print("Response:", response)
    except Exception as e:
        print(f"Error: {e}")