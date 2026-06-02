"""
JARVIS Main Orchestrator — core/jarvis.py
Combines: AI Brain + Memory + Search + Skills + Language Manager.
Supports: English, Malayalam (മലയാളം), Hindi (हिंदी), Tamil (தமிழ்).
"""

import re
import time
import logging
import asyncio
import platform
from typing import Optional, AsyncIterator

import yaml

from .brain import Brain, Message, Role
from .memory import Memory
from .search import WebSearch
from .language import LanguageManager, SUPPORTED_LANGUAGES

# Skill router (optional)
try:
    from skills.skill_router import SkillRouter
    _SKILLS_AVAILABLE = True
except ImportError:
    _SKILLS_AVAILABLE = False

logger = logging.getLogger("jarvis")


class JARVIS:
    """
    Complete JARVIS AI system — multilingual edition.
    Brain + Memory + Search + Skills + Language (EN/ML/HI/TA).
    """

    VERSION = "1.0.0"

    # Search trigger words across all 4 languages
    SEARCH_TRIGGERS = {
        "en": ["latest", "news", "today", "current", "price", "weather",
               "who won", "score", "trending", "recently", "now", "live", "stock"],
        "ml": ["പുതിയ", "വാർത്ത", "ഇന്ന്", "വില", "കാലാവസ്ഥ", "ഇപ്പോൾ"],
        "hi": ["ताजा", "खबर", "आज", "कीमत", "मौसम", "अभी", "लाइव"],
        "ta": ["புதிய", "செய்தி", "இன்று", "விலை", "வானிலை", "இப்போது"],
    }

    def __init__(self, config_path: str = "config/settings.yaml"):
        self.cfg = self._load_config(config_path)
        self.os_name = platform.system()
        self.os_version = platform.version()

        # Language Manager — heart of multilingual support
        lang_cfg = self.cfg.get("language", {})
        self.lang = LanguageManager(lang_cfg)

        # Core subsystems
        self.brain = Brain(config_path)
        self.memory = Memory(self.cfg.get("memory", {}))
        self.search = WebSearch(self.cfg.get("search", {}))

        # Skill router
        if _SKILLS_AVAILABLE:
            self.skills = SkillRouter(self, self.cfg)
        else:
            self.skills = None
        self.pc = getattr(self.skills, "pc", None) if self.skills else None

        logger.info(
            f"JARVIS v{self.VERSION} | OS: {self.os_name} | "
            f"Lang: {self.lang.current_lang} | "
            f"Supported: {[l['name'] for l in self.lang.list_languages()]}"
        )

    # ── Config ─────────────────────────────────────────────

    def _load_config(self, path: str) -> dict:
        try:
            with open(path) as f:
                return yaml.safe_load(f) or {}
        except FileNotFoundError:
            return {}

    # ── System prompt ───────────────────────────────────────

    def _build_system_prompt(self, user_lang: str = "en") -> str:
        base = self.cfg.get("system", {}).get("personality",
            "You are JARVIS, an advanced AI assistant.")

        lang_info = SUPPORTED_LANGUAGES.get(user_lang, SUPPORTED_LANGUAGES["en"])
        lang_instruction = (
            f"\n\nIMPORTANT: The user is communicating in {lang_info['name']} "
            f"({lang_info['native']}). Always respond in {lang_info['name']} "
            f"using the correct script and natural phrasing. "
            f"Be culturally aware and contextually appropriate."
            if user_lang != "en" else ""
        )

        facts = self.memory.get_user_facts()
        os_info = f"User's OS: {self.os_name}."
        parts = [base + lang_instruction, os_info]
        if facts:
            parts.append("Known facts:\n" + "\n".join(f"- {f}" for f in facts[:5]))
        return "\n\n".join(parts)

    # ── Search need detection (multilingual) ────────────────

    def _needs_search(self, text: str, lang: str = "en") -> bool:
        text_lower = text.lower()
        triggers = self.SEARCH_TRIGGERS.get(lang, []) + self.SEARCH_TRIGGERS["en"]
        return any(t in text_lower for t in triggers)

    # ── Main chat entry point ───────────────────────────────

    async def chat(
        self,
        user_input: str,
        stream: bool = False,
        input_lang: str = None,       # Force input language (None = auto-detect)
        response_lang: str = None,    # Force response language (None = same as input)
    ) -> "str | AsyncIterator[str]":
        """
        Multilingual chat entry point.
        1. Detect input language
        2. Translate input → English for AI brain
        3. Try skill router (PC commands, productivity, etc.)
        4. Call AI brain with English input
        5. Translate response → user's language
        6. Store both original and English versions in memory
        """
        # ── Step 1: Detect language ──────────────────────────
        detected_lang = input_lang or self.lang.detect(user_input)
        resp_lang = response_lang or detected_lang
        self.lang.current_lang = detected_lang

        # ── Step 2: Translate input → English ───────────────
        if detected_lang != "en":
            translation = await self.lang.translate(user_input, "en", detected_lang)
            input_en = translation.text
            logger.info(f"[{detected_lang}→EN] '{user_input[:50]}' → '{input_en[:50]}'")
        else:
            input_en = user_input

        # ── Step 3: Skill router fast-path (use English input) ──
        if self.skills:
            skill_result = await self.skills.handle(input_en)
            if skill_result is not None:
                # Translate skill result back to user language
                final = await self._localize(skill_result, resp_lang)
                self.memory.remember(user_input, role="user")
                self.memory.remember(final, role="assistant")
                if stream:
                    async def _skill_stream():
                        yield final
                    return _skill_stream()
                return final

        # ── Step 4: Build context and call AI brain ──────────
        self.memory.remember(user_input, role="user")

        context_messages = self.memory.get_context()
        recalled = self.memory.recall(input_en, n=3)
        recalled_text = "\n".join(
            f"[Memory] {e.content}" for e in recalled
            if e.content not in (user_input, input_en)
            and e.role not in ("user", "assistant")
        )

        search_context = ""
        if self._needs_search(input_en, "en") or self._needs_search(user_input, detected_lang):
            logger.info("Triggering multilingual web search...")
            sr = await self.search.search(input_en, n=4)
            if sr.success:
                search_context = self.search.format_for_llm(sr)

        system = self._build_system_prompt(resp_lang)
        if recalled_text:
            system += f"\n\nRelevant memories:\n{recalled_text}"
        if search_context:
            system += f"\n\nLive web data:\n{search_context}"

        messages = [Message(role=Role(m["role"]), content=m["content"])
                    for m in context_messages]

        # ── Step 5: Stream or full response ──────────────────
        if stream:
            full_response = []

            async def _stream_and_localize():
                en_buffer = []
                async for token in await self.brain.think(
                    messages, system_override=system, stream=True
                ):
                    en_buffer.append(token)
                    yield token   # Stream English tokens while collecting

                en_text = "".join(en_buffer)
                self.memory.remember(en_text, role="assistant")

                # After streaming: translate full response and yield as final block
                if resp_lang != "en":
                    localized = await self._localize(en_text, resp_lang)
                    yield f"\n\n[{SUPPORTED_LANGUAGES[resp_lang]['native']}]\n{localized}"

            return _stream_and_localize()
        else:
            response = await self.brain.think(messages, system_override=system)
            en_text = response.text

            # Translate to user language
            final = await self._localize(en_text, resp_lang)
            self.memory.remember(final, role="assistant")
            return final

    # ── Localization helper ─────────────────────────────────

    async def _localize(self, text: str, lang: str) -> str:
        """Translate English text to target language. Returns original if English."""
        if lang == "en" or not text.strip():
            return text
        result = await self.lang.from_english(text, lang)
        return result

    # ── Convenience methods ──────────────────────────────────

    async def ask(self, question: str, lang: str = None) -> str:
        """Simple non-streaming ask. Auto-detects language if not specified."""
        return await self.chat(question, stream=False, input_lang=lang)

    async def ask_in(self, question: str, lang: str) -> str:
        """Ask in a specific language and get response in same language."""
        return await self.chat(question, input_lang=lang, response_lang=lang)

    def set_language(self, lang_code: str) -> str:
        """Manually set the active language. Returns confirmation."""
        self.lang.set_language(lang_code)
        info = SUPPORTED_LANGUAGES.get(lang_code, {})
        return info.get("greeting", f"Language set to {lang_code}.")

    def detect_language(self, text: str) -> dict:
        """Detect language of text and return info dict."""
        code = self.lang.detect(text)
        return {"code": code, **SUPPORTED_LANGUAGES.get(code, {})}

    # ── Status ──────────────────────────────────────────────

    async def status(self) -> dict:
        brain_status = await self.brain.status()
        return {
            "version": self.VERSION,
            "os": self.os_name,
            "brain": brain_status,
            "memory": self.memory.stats(),
            "search_providers": list(self.search.providers.keys()),
            "skills": self.skills.status() if self.skills else {},
            "language": {
                "current": self.lang.current_lang,
                "supported": self.lang.list_languages(),
                "translators": [
                    t.name for t in self.lang._translators if t.enabled
                ],
            },
        }

    def start_skills(self, loop=None):
        if self.skills:
            self.skills.start(loop)

    def stop_skills(self):
        if self.skills:
            self.skills.stop()

    def new_conversation(self):
        self.memory.new_session()
        logger.info("New conversation started.")

    async def learn(self, fact: str) -> str:
        self.memory.remember_fact(fact)
        return f"Noted: {fact}"


# ─────────────────────────────────────────────
# Multilingual interactive CLI
# ─────────────────────────────────────────────

async def interactive_cli():
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s | %(message)s")

    print("\n" + "═" * 55)
    print("  🤖  J.A.R.V.I.S  — Multilingual AI Assistant")
    print("═" * 55)

    jarvis = JARVIS()
    status = await jarvis.status()

    lang_info = status["language"]
    providers_alive = [k for k, v in status["brain"].items() if v]
    translators = lang_info["translators"]

    print(f"\n  OS        : {status['os']}")
    print(f"  AI        : {', '.join(providers_alive) or 'none'}")
    print(f"  Languages : {', '.join(l['name'] for l in lang_info['supported'])}")
    print(f"  Translators: {', '.join(translators) or 'none (translation disabled)'}")
    print(f"  Memory    : {status['memory']['total_memories']} entries")
    print()
    print("  Commands: 'quit' · 'lang ml/hi/ta/en' · 'new' · 'status'")
    print("  Type in English, Malayalam, Hindi, or Tamil — JARVIS auto-detects!")
    print("─" * 55 + "\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\nJARVIS: Goodbye!")
            break

        if not user_input:
            continue

        if user_input.lower() in ("quit", "exit", "bye", "നന്ദി", "अलविदा", "போய்விடு"):
            print("JARVIS: Goodbye! / अलविदा! / വിട! / போய்விடுகிறேன்!")
            break

        if user_input.lower() == "new":
            jarvis.new_conversation()
            print("JARVIS: New conversation started.\n")
            continue

        if user_input.lower() == "status":
            s = await jarvis.status()
            print(f"JARVIS: {s}\n")
            continue

        # Manual language switch: "lang ml", "lang hi", etc.
        if user_input.lower().startswith("lang "):
            code = user_input.split()[-1].lower()
            msg = jarvis.set_language(code)
            print(f"JARVIS: {msg}\n")
            continue

        # Show detected language
        detected = jarvis.lang.detect(user_input)
        lang_name = SUPPORTED_LANGUAGES.get(detected, {}).get("name", "English")
        print(f"  [{lang_name}] ", end="", flush=True)

        print("JARVIS: ", end="", flush=True)
        async for token in await jarvis.chat(user_input, stream=True):
            print(token, end="", flush=True)
        print("\n")


if __name__ == "__main__":
    asyncio.run(interactive_cli())
