"""
JARVIS AI Brain — core/brain.py
Manages all AI providers with intelligent fallback routing.
Supports: Ollama, Anthropic, OpenAI, Groq, Google, Mistral, Cohere, Together, HuggingFace
"""

import os
import json
import time
import logging
import asyncio
import platform
from typing import Optional, AsyncIterator
from dataclasses import dataclass, field
from enum import Enum

import yaml

logger = logging.getLogger("jarvis.brain")

# ─────────────────────────────────────────────
# Data models
# ─────────────────────────────────────────────

class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


@dataclass
class Message:
    role: Role
    content: str
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {"role": self.role.value, "content": self.content}


@dataclass
class BrainResponse:
    text: str
    provider: str
    model: str
    tokens_used: int = 0
    latency_ms: float = 0.0
    success: bool = True
    error: Optional[str] = None


# ─────────────────────────────────────────────
# Individual provider clients
# ─────────────────────────────────────────────

class OllamaProvider:
    """Local Ollama inference — fully offline."""

    name = "ollama"

    def __init__(self, cfg: dict):
        self.base_url = cfg.get("base_url", "http://localhost:11434")
        self.model = cfg.get("model", "llama3.2")
        self.timeout = cfg.get("timeout", 30)
        self.enabled = cfg.get("enabled", True)

    async def is_available(self) -> bool:
        try:
            import aiohttp
            async with aiohttp.ClientSession() as s:
                async with s.get(f"{self.base_url}/api/tags", timeout=aiohttp.ClientTimeout(total=3)) as r:
                    return r.status == 200
        except Exception:
            return False

    async def chat(self, messages: list[dict], system: str = "") -> BrainResponse:
        import aiohttp
        t0 = time.time()
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": 0.7}
        }
        if system:
            payload["system"] = system
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    f"{self.base_url}/api/chat",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=self.timeout)
                ) as r:
                    data = await r.json()
                    text = data["message"]["content"]
                    return BrainResponse(
                        text=text, provider=self.name, model=self.model,
                        latency_ms=(time.time() - t0) * 1000
                    )
        except Exception as e:
            return BrainResponse(text="", provider=self.name, model=self.model, success=False, error=str(e))

    async def stream(self, messages: list[dict], system: str = "") -> AsyncIterator[str]:
        import aiohttp
        payload = {"model": self.model, "messages": messages, "stream": True}
        if system:
            payload["system"] = system
        async with aiohttp.ClientSession() as s:
            async with s.post(f"{self.base_url}/api/chat", json=payload) as r:
                async for line in r.content:
                    chunk = json.loads(line)
                    if token := chunk.get("message", {}).get("content", ""):
                        yield token
                    if chunk.get("done"):
                        break


class AnthropicProvider:
    """Anthropic Claude — highest quality reasoning."""

    name = "anthropic"

    def __init__(self, cfg: dict):
        self.api_key = cfg.get("api_key") or os.getenv("ANTHROPIC_API_KEY", "")
        self.model = cfg.get("model", "claude-opus-4-5")
        self.max_tokens = cfg.get("max_tokens", 4096)
        self.enabled = cfg.get("enabled", True) and bool(self.api_key)

    async def chat(self, messages: list[dict], system: str = "") -> BrainResponse:
        if not self.api_key:
            return BrainResponse(text="", provider=self.name, model=self.model, success=False, error="No API key")
        t0 = time.time()
        try:
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=self.api_key)
            kwargs = {"model": self.model, "max_tokens": self.max_tokens, "messages": messages}
            if system:
                kwargs["system"] = system
            response = await client.messages.create(**kwargs)
            text = response.content[0].text
            tokens = response.usage.input_tokens + response.usage.output_tokens
            return BrainResponse(text=text, provider=self.name, model=self.model,
                                 tokens_used=tokens, latency_ms=(time.time() - t0) * 1000)
        except Exception as e:
            return BrainResponse(text="", provider=self.name, model=self.model, success=False, error=str(e))

    async def stream(self, messages: list[dict], system: str = "") -> AsyncIterator[str]:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=self.api_key)
        kwargs = {"model": self.model, "max_tokens": self.max_tokens, "messages": messages, "stream": True}
        if system:
            kwargs["system"] = system
        async with client.messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                yield text


class OpenAICompatProvider:
    """OpenAI-compatible provider (OpenAI, Groq, Together, Mistral, etc.)"""

    def __init__(self, name: str, cfg: dict, base_url: str = None):
        self.name = name
        self.api_key = cfg.get("api_key") or os.getenv(f"{name.upper()}_API_KEY", "")
        self.model = cfg.get("model", "gpt-4o")
        self.base_url = cfg.get("base_url") or base_url or "https://api.openai.com/v1"
        self.enabled = cfg.get("enabled", True) and bool(self.api_key)

    async def chat(self, messages: list[dict], system: str = "") -> BrainResponse:
        if not self.api_key:
            return BrainResponse(text="", provider=self.name, model=self.model, success=False, error="No API key")
        t0 = time.time()
        try:
            import openai
            client = openai.AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)
            msg = []
            if system:
                msg.append({"role": "system", "content": system})
            msg.extend(messages)
            response = await client.chat.completions.create(model=self.model, messages=msg, temperature=0.7)
            text = response.choices[0].message.content
            tokens = response.usage.total_tokens if response.usage else 0
            return BrainResponse(text=text, provider=self.name, model=self.model,
                                 tokens_used=tokens, latency_ms=(time.time() - t0) * 1000)
        except Exception as e:
            return BrainResponse(text="", provider=self.name, model=self.model, success=False, error=str(e))

    async def stream(self, messages: list[dict], system: str = "") -> AsyncIterator[str]:
        import openai
        client = openai.AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)
        msg = []
        if system:
            msg.append({"role": "system", "content": system})
        msg.extend(messages)
        async with client.chat.completions.create(model=self.model, messages=msg, stream=True) as stream:
            async for chunk in stream:
                if token := (chunk.choices[0].delta.content or ""):
                    yield token


class GoogleProvider:
    """Google Gemini."""

    name = "google"

    def __init__(self, cfg: dict):
        self.api_key = cfg.get("api_key") or os.getenv("GOOGLE_API_KEY", "")
        self.model = cfg.get("model", "gemini-2.0-flash")
        self.enabled = cfg.get("enabled", True) and bool(self.api_key)

    async def chat(self, messages: list[dict], system: str = "") -> BrainResponse:
        if not self.api_key:
            return BrainResponse(text="", provider=self.name, model=self.model, success=False, error="No API key")
        t0 = time.time()
        try:
            import google.generativeai as genai
            genai.configure(api_key=self.api_key)
            model = genai.GenerativeModel(self.model, system_instruction=system or None)
            history = []
            for m in messages[:-1]:
                history.append({"role": "model" if m["role"] == "assistant" else "user", "parts": [m["content"]]})
            chat = model.start_chat(history=history)
            response = await asyncio.to_thread(chat.send_message, messages[-1]["content"])
            return BrainResponse(text=response.text, provider=self.name, model=self.model,
                                 latency_ms=(time.time() - t0) * 1000)
        except Exception as e:
            return BrainResponse(text="", provider=self.name, model=self.model, success=False, error=str(e))

    async def stream(self, messages: list[dict], system: str = "") -> AsyncIterator[str]:
        import google.generativeai as genai
        genai.configure(api_key=self.api_key)
        model = genai.GenerativeModel(self.model, system_instruction=system or None)
        response = await asyncio.to_thread(model.generate_content, messages[-1]["content"], stream=True)
        for chunk in response:
            if chunk.text:
                yield chunk.text


class CohereProvider:
    """Cohere Command models."""

    name = "cohere"

    def __init__(self, cfg: dict):
        self.api_key = cfg.get("api_key") or os.getenv("COHERE_API_KEY", "")
        self.model = cfg.get("model", "command-r-plus")
        self.enabled = cfg.get("enabled", True) and bool(self.api_key)

    async def chat(self, messages: list[dict], system: str = "") -> BrainResponse:
        if not self.api_key:
            return BrainResponse(text="", provider=self.name, model=self.model, success=False, error="No API key")
        t0 = time.time()
        try:
            import cohere
            client = cohere.AsyncClientV2(api_key=self.api_key)
            msgs = []
            if system:
                msgs.append({"role": "system", "content": system})
            msgs.extend(messages)
            response = await client.chat(model=self.model, messages=msgs)
            text = response.message.content[0].text
            return BrainResponse(text=text, provider=self.name, model=self.model,
                                 latency_ms=(time.time() - t0) * 1000)
        except Exception as e:
            return BrainResponse(text="", provider=self.name, model=self.model, success=False, error=str(e))

    async def stream(self, messages: list[dict], system: str = "") -> AsyncIterator[str]:
        import cohere
        client = cohere.AsyncClientV2(api_key=self.api_key)
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend(messages)
        async for event in client.chat_stream(model=self.model, messages=msgs):
            if hasattr(event, "delta") and event.delta and event.delta.message:
                if event.delta.message.content:
                    yield event.delta.message.content[0].text


class HuggingFaceProvider:
    """HuggingFace Inference API."""

    name = "huggingface"

    def __init__(self, cfg: dict):
        self.api_key = cfg.get("api_key") or os.getenv("HUGGINGFACE_API_KEY", "")
        self.model = cfg.get("model", "meta-llama/Meta-Llama-3.1-70B-Instruct")
        self.enabled = cfg.get("enabled", True) and bool(self.api_key)

    async def chat(self, messages: list[dict], system: str = "") -> BrainResponse:
        if not self.api_key:
            return BrainResponse(text="", provider=self.name, model=self.model, success=False, error="No API key")
        t0 = time.time()
        try:
            from huggingface_hub import AsyncInferenceClient
            client = AsyncInferenceClient(api_key=self.api_key)
            msgs = []
            if system:
                msgs.append({"role": "system", "content": system})
            msgs.extend(messages)
            response = await client.chat.completions.create(model=self.model, messages=msgs, max_tokens=2048)
            text = response.choices[0].message.content
            return BrainResponse(text=text, provider=self.name, model=self.model,
                                 latency_ms=(time.time() - t0) * 1000)
        except Exception as e:
            return BrainResponse(text="", provider=self.name, model=self.model, success=False, error=str(e))

    async def stream(self, messages: list[dict], system: str = "") -> AsyncIterator[str]:
        from huggingface_hub import AsyncInferenceClient
        client = AsyncInferenceClient(api_key=self.api_key)
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend(messages)
        stream = await client.chat.completions.create(model=self.model, messages=msgs, stream=True)
        async for chunk in stream:
            if token := (chunk.choices[0].delta.content or ""):
                yield token


# ─────────────────────────────────────────────
# The Brain — orchestrates all providers
# ─────────────────────────────────────────────

class Brain:
    """
    JARVIS AI Brain.
    Routes requests through AI providers in priority order,
    falling back gracefully when one is unavailable or errors.
    """

    def __init__(self, config_path: str = "config/settings.yaml"):
        self.cfg = self._load_config(config_path)
        self.system_prompt = self.cfg.get("system", {}).get("personality", "You are JARVIS, an AI assistant.")
        self.providers: dict = {}
        self.priority: list[str] = []
        self._build_providers()
        self._os = platform.system()  # "Windows" or "Linux"
        logger.info(f"JARVIS Brain initialized on {self._os} with providers: {self.priority}")

    def _load_config(self, path: str) -> dict:
        try:
            with open(path) as f:
                return yaml.safe_load(f)
        except FileNotFoundError:
            logger.warning(f"Config not found at {path}, using defaults.")
            return {}

    def _build_providers(self):
        ai_cfg = self.cfg.get("ai", {})
        self.priority = ai_cfg.get("provider_priority", ["ollama", "anthropic", "openai"])

        builders = {
            "ollama":      lambda: OllamaProvider(ai_cfg.get("ollama", {})),
            "anthropic":   lambda: AnthropicProvider(ai_cfg.get("anthropic", {})),
            "openai":      lambda: OpenAICompatProvider("openai", ai_cfg.get("openai", {})),
            "groq":        lambda: OpenAICompatProvider("groq", ai_cfg.get("groq", {}),
                                                         "https://api.groq.com/openai/v1"),
            "together":    lambda: OpenAICompatProvider("together", ai_cfg.get("together", {}),
                                                         "https://api.together.xyz/v1"),
            "mistral":     lambda: OpenAICompatProvider("mistral", ai_cfg.get("mistral", {}),
                                                         "https://api.mistral.ai/v1"),
            "google":      lambda: GoogleProvider(ai_cfg.get("google", {})),
            "cohere":      lambda: CohereProvider(ai_cfg.get("cohere", {})),
            "huggingface": lambda: HuggingFaceProvider(ai_cfg.get("huggingface", {})),
        }

        for name in self.priority:
            if name in builders:
                try:
                    provider = builders[name]()
                    if getattr(provider, "enabled", True):
                        self.providers[name] = provider
                        logger.debug(f"Provider loaded: {name}")
                except Exception as e:
                    logger.warning(f"Could not load provider {name}: {e}")

    # ── Public API ──────────────────────────────

    async def think(
        self,
        messages: list[Message],
        system_override: str = None,
        force_provider: str = None,
        stream: bool = False
    ) -> BrainResponse | AsyncIterator[str]:
        """
        Main entry point. Send messages and get a response.
        Automatically falls back through providers on failure.
        """
        msg_dicts = [m.to_dict() for m in messages]
        system = system_override or self.system_prompt

        if stream:
            return self._stream_first_available(msg_dicts, system, force_provider)
        else:
            return await self._chat_with_fallback(msg_dicts, system, force_provider)

    async def _chat_with_fallback(self, messages: list[dict], system: str, force_provider: str = None) -> BrainResponse:
        order = [force_provider] if force_provider else self.priority

        for name in order:
            provider = self.providers.get(name)
            if not provider:
                continue

            # Ollama needs availability check
            if name == "ollama":
                if not await provider.is_available():
                    logger.info("Ollama not running, skipping.")
                    continue

            logger.info(f"Trying provider: {name}")
            response = await provider.chat(messages, system)

            if response.success:
                logger.info(f"✓ Response from {name} in {response.latency_ms:.0f}ms")
                self._last_provider = name
                self._last_model = response.model
                return response
            else:
                logger.warning(f"✗ {name} failed: {response.error}")

        return BrainResponse(
            text="I'm sorry, all AI providers are currently unavailable. Please check your configuration.",
            provider="none", model="none", success=False,
            error="All providers exhausted"
        )

    async def _stream_first_available(self, messages: list[dict], system: str, force_provider: str = None):
        order = [force_provider] if force_provider else self.priority
        for name in order:
            provider = self.providers.get(name)
            if not provider:
                continue
            if name == "ollama" and not await provider.is_available():
                continue
            if hasattr(provider, "stream"):
                logger.info(f"Streaming from: {name}")
                async for token in provider.stream(messages, system):
                    yield token
                return
        yield "All providers unavailable."

    # ── Convenience helpers ──────────────────────

    async def quick_ask(self, question: str) -> str:
        """Simple one-shot question, returns text."""
        msgs = [Message(role=Role.USER, content=question)]
        response = await self.think(msgs)
        return response.text

    async def status(self) -> dict:
        """Check which providers are alive."""
        results = {}
        for name, provider in self.providers.items():
            if name == "ollama":
                results[name] = await provider.is_available()
            else:
                results[name] = getattr(provider, "enabled", False)
        return results

    def list_providers(self) -> list[str]:
        return list(self.providers.keys())


# ─────────────────────────────────────────────
# CLI test runner
# ─────────────────────────────────────────────

async def _demo():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")
    brain = Brain()

    print("\n🤖 JARVIS Brain — Provider Status")
    print("─" * 40)
    status = await brain.status()
    for provider, alive in status.items():
        icon = "✅" if alive else "❌"
        print(f"  {icon} {provider}")

    print("\n💬 Asking JARVIS a test question...")
    print("─" * 40)
    response = await brain.quick_ask("In one sentence, what is your purpose?")
    print(f"\nJARVIS [{response.provider}/{response.model}]:")
    print(f"  {response.text}")
    print(f"\n  ⏱  {response.latency_ms:.0f}ms | 🔢 {response.tokens_used} tokens")

    print("\n🌊 Streaming test...")
    print("─" * 40)
    print("JARVIS: ", end="", flush=True)
    msgs = [Message(role=Role.USER, content="Say hello in exactly 10 words.")]
    async for token in await brain.think(msgs, stream=True):
        print(token, end="", flush=True)
    print("\n")


if __name__ == "__main__":
    asyncio.run(_demo())
