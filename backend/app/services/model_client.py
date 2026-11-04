from __future__ import annotations

import os

import httpx


class OpenAICompatibleModel:
    def __init__(self) -> None:
        ollama_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
        self.base_url = os.getenv("ANVESHA_MODEL_BASE_URL", f"{ollama_url}/v1").rstrip("/")
        self.model = os.getenv("ANVESHA_MODEL_NAME", "llama3.2")
        self.api_key = os.getenv("ANVESHA_MODEL_API_KEY") or os.getenv("OPENAI_API_KEY")

    def generate(self, prompt: str) -> str:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        response = httpx.post(
            f"{self.base_url}/chat/completions",
            headers=headers,
            json={
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
            },
            timeout=3.0,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise ValueError("The configured model returned an empty response")
        return content.strip()
