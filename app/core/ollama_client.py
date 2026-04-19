import json
import re
from typing import Any, Dict, List

import requests

from app.utils.config import config


class OllamaClient:
    def __init__(self):
        self.base_url = config.OLLAMA_BASE_URL.rstrip("/")
        self.model = config.OLLAMA_MODEL
        self.timeout = config.OLLAMA_TIMEOUT

    def chat(self, prompt: str, max_tokens: int = 1024, temperature: float = 0.0, json_mode: bool = False) -> str:
        messages = [{"role": "user", "content": prompt}]
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }

        if json_mode:
            payload["format"] = "json"

        try:
            response = requests.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(
                f"Failed to call Ollama at {self.base_url}. Make sure Ollama is running and the model '{self.model}' is available."
            ) from exc

        data = response.json()
        message = data.get("message") or {}
        content = message.get("content")
        if not isinstance(content, str):
            raise RuntimeError("Ollama response did not include message content.")

        return content.strip()

    def chat_json(self, prompt: str, max_tokens: int = 1024, temperature: float = 0.0) -> Any:
        content = self.chat(prompt, max_tokens=max_tokens, temperature=temperature, json_mode=True)
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return self._extract_json_payload(content)

    @staticmethod
    def _extract_json_payload(content: str) -> Any:
        cleaned = re.sub(r"```(?:json)?", "", content, flags=re.IGNORECASE).replace("```", "").strip()
        decoder = json.JSONDecoder()

        for match in re.finditer(r"[\[{]", cleaned):
            try:
                payload, _ = decoder.raw_decode(cleaned[match.start():])
                return payload
            except json.JSONDecodeError:
                continue

        raise ValueError("No JSON payload found in Ollama response.")


ollama_client = OllamaClient()