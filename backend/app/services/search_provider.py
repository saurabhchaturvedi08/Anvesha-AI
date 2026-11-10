from __future__ import annotations

import os
from typing import Any

import httpx
from dotenv import load_dotenv


class TavilySearchProvider:
    def __init__(self, api_key: str | None = None) -> None:
        load_dotenv()
        self.api_key = api_key if api_key is not None else os.getenv("TAVILY_API_KEY", "")

    def search(self, query: str, max_results: int = 5) -> list[dict[str, str]]:
        if not self.api_key:
            return []

        response = httpx.post(
            "https://api.tavily.com/search",
            json={
                "api_key": self.api_key,
                "query": query,
                "search_depth": "basic",
                "max_results": max_results,
                "include_answer": False,
            },
            timeout=15.0,
        )
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        results = []
        for item in payload.get("results", []):
            url = item.get("url")
            title = item.get("title")
            if not isinstance(url, str) or not isinstance(title, str):
                continue
            results.append(
                {
                    "title": title[:500],
                    "url": url[:2048],
                    "snippet": str(item.get("content", ""))[:4000],
                }
            )
        return results
