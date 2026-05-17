import os
import json
import logging
import requests
from typing import List, Dict

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class NvidiaClient:
    """
    Client for Nvidia NIM API.
    Uses the Nvidia Inference Microservices endpoint to run models.
    """

    def __init__(self):
        self.api_key = os.getenv("NVCF_API_KEY")
        if not self.api_key:
            raise ValueError("NVCF_API_KEY environment variable is required")

        # Nvidia chat completions endpoint (free tier)
        self.base_url = "https://integrate.api.nvidia.com/v1/chat/completions"

    def _make_request(self, model: str, messages: List[Dict]) -> Dict:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": 1000,
            "temperature": 0.7,
            "top_p": 0.95
        }

        try:
            response = requests.post(
                self.base_url,
                headers=headers,
                json=payload,
                timeout=60
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Nvidia API request failed: {e}")
            raise
        except json.JSONDecodeError:
            logger.error("Invalid JSON response from Nvidia API")
            raise

    def chat(self, messages: List[Dict], model: str = "meta/llama-3.1-8b-instruct") -> str:
        """
        Send a chat request to Nvidia NIM.

        Args:
            messages: List of message dicts with 'role' and 'content'
            model: Model name (default: free-tier Llama 3.1 8B)

        Returns:
            The content of the final assistant message
        """
        try:
            response = self._make_request(model, messages)
            choices = response.get("choices", [])
            if not choices:
                raise ValueError("No choices in Nvidia API response")
            return choices[0]["message"]["content"]
        except Exception as e:
            logger.error(f"Error in Nvidia chat: {e}")
            raise


if __name__ == "__main__":
    client = NvidiaClient()
    messages = [{"role": "user", "content": "Hello! How are you?"}]
    try:
        response = client.chat(messages)
        print("Response:", response)
    except Exception as e:
        print(f"Error: {e}")