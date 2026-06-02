"""
JARVIS Web Search — core/search.py
Multi-provider web search: Tavily, Brave, Serper, DuckDuckGo.
Falls back automatically. Returns clean, structured results.
"""

import os
import time
import logging
import asyncio
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("jarvis.search")


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    score: float = 1.0
    source: str = ""


@dataclass
class SearchResponse:
    query: str
    results: list[SearchResult]
    provider: str
    answer: Optional[str] = None   # AI-generated answer (Tavily feature)
    latency_ms: float = 0.0
    success: bool = True


class TavilySearch:
    name = "tavily"

    def __init__(self, cfg: dict):
        self.api_key = cfg.get("api_key") or os.getenv("TAVILY_API_KEY", "")
        self.enabled = bool(self.api_key)

    async def search(self, query: str, n: int = 5) -> SearchResponse:
        t0 = time.time()
        try:
            from tavily import AsyncTavilyClient
            client = AsyncTavilyClient(api_key=self.api_key)
            response = await client.search(query, max_results=n, include_answer=True)
            results = [
                SearchResult(
                    title=r.get("title", ""),
                    url=r.get("url", ""),
                    snippet=r.get("content", ""),
                    score=r.get("score", 1.0),
                    source=self.name
                )
                for r in response.get("results", [])
            ]
            return SearchResponse(
                query=query, results=results, provider=self.name,
                answer=response.get("answer"),
                latency_ms=(time.time() - t0) * 1000
            )
        except Exception as e:
            logger.warning(f"Tavily error: {e}")
            return SearchResponse(query=query, results=[], provider=self.name, success=False)


class BraveSearch:
    name = "brave"

    def __init__(self, cfg: dict):
        self.api_key = cfg.get("api_key") or os.getenv("BRAVE_API_KEY", "")
        self.enabled = bool(self.api_key)

    async def search(self, query: str, n: int = 5) -> SearchResponse:
        import aiohttp
        t0 = time.time()
        try:
            url = "https://api.search.brave.com/res/v1/web/search"
            headers = {"Accept": "application/json", "X-Subscription-Token": self.api_key}
            params = {"q": query, "count": n}
            async with aiohttp.ClientSession() as s:
                async with s.get(url, headers=headers, params=params) as r:
                    data = await r.json()
            results = [
                SearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("description", ""),
                    source=self.name
                )
                for item in data.get("web", {}).get("results", [])[:n]
            ]
            return SearchResponse(query=query, results=results, provider=self.name,
                                  latency_ms=(time.time() - t0) * 1000)
        except Exception as e:
            logger.warning(f"Brave search error: {e}")
            return SearchResponse(query=query, results=[], provider=self.name, success=False)


class SerperSearch:
    name = "serper"

    def __init__(self, cfg: dict):
        self.api_key = cfg.get("api_key") or os.getenv("SERPER_API_KEY", "")
        self.enabled = bool(self.api_key)

    async def search(self, query: str, n: int = 5) -> SearchResponse:
        import aiohttp
        t0 = time.time()
        try:
            url = "https://google.serper.dev/search"
            headers = {"X-API-KEY": self.api_key, "Content-Type": "application/json"}
            payload = {"q": query, "num": n}
            async with aiohttp.ClientSession() as s:
                async with s.post(url, json=payload, headers=headers) as r:
                    data = await r.json()
            results = [
                SearchResult(
                    title=item.get("title", ""),
                    url=item.get("link", ""),
                    snippet=item.get("snippet", ""),
                    source=self.name
                )
                for item in data.get("organic", [])[:n]
            ]
            answer = data.get("answerBox", {}).get("answer")
            return SearchResponse(query=query, results=results, provider=self.name,
                                  answer=answer, latency_ms=(time.time() - t0) * 1000)
        except Exception as e:
            logger.warning(f"Serper error: {e}")
            return SearchResponse(query=query, results=[], provider=self.name, success=False)


class DuckDuckGoSearch:
    """Free search, no API key needed."""
    name = "duckduckgo"
    enabled = True

    async def search(self, query: str, n: int = 5) -> SearchResponse:
        t0 = time.time()
        try:
            from duckduckgo_search import AsyncDDGS
            async with AsyncDDGS() as ddgs:
                raw = await ddgs.atext(query, max_results=n)
            results = [
                SearchResult(
                    title=r.get("title", ""),
                    url=r.get("href", ""),
                    snippet=r.get("body", ""),
                    source=self.name
                )
                for r in raw
            ]
            return SearchResponse(query=query, results=results, provider=self.name,
                                  latency_ms=(time.time() - t0) * 1000)
        except Exception as e:
            logger.warning(f"DuckDuckGo error: {e}")
            return SearchResponse(query=query, results=[], provider=self.name, success=False)


class WebSearch:
    """JARVIS Web Search with provider fallback."""

    def __init__(self, config: dict = None):
        cfg = config or {}
        self.priority = cfg.get("provider_priority", ["tavily", "brave", "serper", "duckduckgo"])
        providers_map = {
            "tavily":     TavilySearch(cfg.get("tavily", {})),
            "brave":      BraveSearch(cfg.get("brave", {})),
            "serper":     SerperSearch(cfg.get("serper", {})),
            "duckduckgo": DuckDuckGoSearch(),
        }
        self.providers = {name: p for name, p in providers_map.items() if getattr(p, "enabled", True)}
        logger.info(f"WebSearch ready with providers: {list(self.providers.keys())}")

    async def search(self, query: str, n: int = 5) -> SearchResponse:
        for name in self.priority:
            provider = self.providers.get(name)
            if not provider:
                continue
            logger.info(f"Searching [{name}]: {query}")
            response = await provider.search(query, n)
            if response.success and response.results:
                return response
        return SearchResponse(query=query, results=[], provider="none", success=False)

    def format_for_llm(self, response: SearchResponse) -> str:
        """Format results into a string for the LLM."""
        if not response.results:
            return f"No results found for: {response.query}"
        lines = [f"Web search results for: '{response.query}'\n"]
        if response.answer:
            lines.append(f"Quick answer: {response.answer}\n")
        for i, r in enumerate(response.results, 1):
            lines.append(f"{i}. {r.title}")
            lines.append(f"   {r.snippet}")
            lines.append(f"   Source: {r.url}\n")
        return "\n".join(lines)


if __name__ == "__main__":
    async def demo():
        search = WebSearch()
        response = await search.search("latest news in AI today", n=3)
        print(f"\nProvider: {response.provider} | {response.latency_ms:.0f}ms")
        for r in response.results:
            print(f"\n  📰 {r.title}")
            print(f"     {r.snippet[:100]}...")
    asyncio.run(demo())
