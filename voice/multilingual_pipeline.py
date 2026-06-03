"""
JARVIS Multilingual Voice Pipeline — voice/multilingual_pipeline.py
Full multilingual voice loop:
  [wake word] → [record] → [STT + lang detect] → [translate to EN]
  → [AI Brain] → [translate response to user lang] → [TTS in user lang]

Supports switching languages mid-conversation automatically.
"""

import asyncio
import logging
from typing import Optional, Callable

from core.language import LanguageManager, SUPPORTED_LANGUAGES
from .multilingual_stt import MultilingualSTT
from .multilingual_tts import MultilingualTTS
from .wake_word import WakeWordDetector

logger = logging.getLogger("jarvis.voice.multilingual")


class MultilingualPipeline:
    """
    Full multilingual voice pipeline for JARVIS.
    Handles language detection, translation, and language-appropriate TTS.
    """

    def __init__(
        self,
        jarvis_instance,
        language_manager: LanguageManager,
        config: dict = None,
        on_listening: Callable = None,
        on_processing: Callable = None,
        on_response: Callable = None,
        on_language_change: Callable = None,
    ):
        cfg = config or {}
        self.jarvis = jarvis_instance
        self.lm = language_manager
        self.cfg = cfg

        # Callbacks
        self.on_listening = on_listening or (lambda: None)
        self.on_processing = on_processing or (lambda: None)
        self.on_response = on_response or (lambda t, l: None)
        self.on_language_change = on_language_change or (lambda l: None)

        # Sub-systems are initialized lazily so wake word detection can start faster.
        self._stt_cfg = cfg.get("voice", {})
        self._tts_cfg = cfg.get("voice", {})
        self.stt: Optional[MultilingualSTT] = None
        self.tts: Optional[MultilingualTTS] = None
        self.wake = WakeWordDetector(
            config={**cfg.get("voice", {}), "wake_word": self.lm.get_wake_words()},
            on_detected=self._on_wake
        )

        self._running = False
        self._active = False
        self._wake_event = asyncio.Event()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._prev_lang = self.lm.current_lang

        logger.info(f"Multilingual pipeline ready | lang: {self.lm.current_lang}")

    # ── Start / stop ────────────────────────────────────────

    async def start(self):
        self._running = True
        self._loop = asyncio.get_event_loop()
        self._ensure_tts()
        self._ensure_stt()

        # Greet in current language
        greeting = self.lm.get_greeting()
        await self.tts.speak(greeting, self.lm.current_lang)

        # Start wake word listener
        self.wake.start(self._loop)

        # Main loop
        while self._running:
            await self._wake_event.wait()
            self._wake_event.clear()
            if self._running:
                await self._handle_interaction()

        self.wake.stop()

    def stop(self):
        self._running = False
        self.wake.stop()

    def _on_wake(self):
        if not self._active and self._loop:
            self._loop.call_soon_threadsafe(self._wake_event.set)

    # ── Core interaction ────────────────────────────────────

    async def _handle_interaction(self):
        self._active = True
        try:
            self._ensure_stt()
            self.on_listening()

            # Record + transcribe with language detection
            stt_result = await self.stt.listen()

            if not stt_result.success or not stt_result.text.strip():
                error = self.lm.get_error_phrase()
                await self.tts.speak(error, self.lm.current_lang)
                return

            detected = stt_result.detected_lang
            text_original = stt_result.text
            text_english = stt_result.text_english

            logger.info(f"[{detected}] '{text_original}' → EN: '{text_english}'")

            # Notify UI if language changed
            if detected != self._prev_lang:
                logger.info(f"Language switched: {self._prev_lang} → {detected}")
                self.on_language_change(detected)
                self._prev_lang = detected

            self.on_processing()

            # Handle language-switch commands
            if self._is_lang_switch(text_english):
                resp = await self._handle_lang_switch(text_english)
                await self.tts.speak(resp, self.lm.current_lang)
                return

            # Get AI response (always in English)
            thinking = self.lm.get_thinking_phrase(detected)
            # Don't speak "processing" for short delays

            ai_response_en = await self.jarvis.ask(text_english)

            # Translate response to user's language
            if detected != "en":
                translation = await self.lm.from_english(ai_response_en, detected)
                response_text = translation
            else:
                response_text = ai_response_en

            logger.info(f"Response [{detected}]: '{response_text[:80]}'")
            self.on_response(response_text, detected)

            # Speak in user's language
            await self.tts.speak(response_text, detected)

        except Exception as e:
            logger.error(f"Multilingual pipeline error: {e}")
            error_msg = self.lm.get_error_phrase()
            await self.tts.speak(error_msg, self.lm.current_lang)
        finally:
            self._active = False

    # ── Language switching ──────────────────────────────────

    def _is_lang_switch(self, text: str) -> bool:
        """Detect requests to switch the response language."""
        triggers = [
            "switch to", "speak in", "change language", "reply in",
            "talk in", "respond in", "use", "malayalam", "hindi", "tamil", "english"
        ]
        text_lower = text.lower()
        return any(t in text_lower for t in triggers) and any(
            l in text_lower for l in ["malayalam", "hindi", "tamil", "english",
                                       "മലയാളം", "हिंदी", "தமிழ்"]
        )

    async def _handle_lang_switch(self, text: str) -> str:
        """Switch response language and confirm."""
        text_lower = text.lower()
        lang_keywords = {
            "ml": ["malayalam", "മലയാളം"],
            "hi": ["hindi", "हिंदी", "हिन्दी"],
            "ta": ["tamil", "தமிழ்"],
            "en": ["english"],
        }
        for code, keywords in lang_keywords.items():
            if any(k in text_lower for k in keywords):
                self.lm.set_language(code)
                confirmations = {
                    "ml": "ശരി, ഞാൻ ഇനി മലയാളത്തിൽ സംസാരിക്കും.",
                    "hi": "ठीक है, अब मैं हिंदी में बात करूंगा।",
                    "ta": "சரி, இனி தமிழில் பேசுகிறேன்.",
                    "en": "Sure, I'll respond in English from now on.",
                }
                return confirmations.get(code, "Language switched.")
        return "I support English, Malayalam, Hindi, and Tamil."

    def _ensure_stt(self):
        if self.stt is None:
            self.stt = MultilingualSTT(self._stt_cfg, self.lm)

    def _ensure_tts(self):
        if self.tts is None:
            self.tts = MultilingualTTS(self._tts_cfg, self.lm)

    # ── One-shot API ────────────────────────────────────────

    async def listen_once(self) -> tuple[str, str]:
        """Record one utterance. Returns (english_text, detected_lang)."""
        self._ensure_stt()
        result = await self.stt.listen()
        return result.text_english, result.detected_lang

    async def say(self, text: str, lang: str = None):
        """Speak text in given language (or current language)."""
        self._ensure_tts()
        lang = lang or self.lm.current_lang
        await self.tts.speak(text, lang)

    async def say_in_all_languages(self, text_en: str):
        """Say the same message in all supported languages (useful for announcements)."""
        self._ensure_tts()
        for code in ["en", "hi", "ml", "ta"]:
            if code == "en":
                await self.tts.speak(text_en, "en")
            else:
                result = await self.lm.from_english(text_en, code)
                await self.tts.speak(result, code)
            await asyncio.sleep(0.3)

    def status(self) -> dict:
        return {
            "current_lang": self.lm.current_lang,
            "supported": self.lm.list_languages(),
            "running": self._running,
            "active": self._active,
            "wake_backend": self.wake._backend.name,
        }
