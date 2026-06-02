"""
JARVIS Language Manager — core/language.py
Full multilingual support: English, Malayalam (മലയാളം), Hindi (हिंदी), Tamil (தமிழ்).

Features:
  - Auto language detection from speech or text
  - Translate user input → English for AI brain
  - Translate AI response → user's language
  - Language-specific TTS voice routing
  - Multilingual wake word variants
  - Script-aware text handling (Devanagari, Malayalam, Tamil scripts)

Providers (with fallback):
  Primary:  Google Cloud Translate API
  Fallback: Deep Translator (googletrans wrapper, free)
  Fallback: LibreTranslate (self-hosted, free)
  Fallback: Argos Translate (local, offline)
"""

import re
import os
import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("jarvis.language")


# ─────────────────────────────────────────────
# Language definitions
# ─────────────────────────────────────────────

SUPPORTED_LANGUAGES = {
    "en": {
        "name": "English",
        "native": "English",
        "whisper_code": "en",
        "google_tts_code": "en-IN",
        "gtts_code": "en",
        "indic_code": None,
        "wake_words": ["jarvis", "hey jarvis", "ok jarvis"],
        "greeting": "Good day, sir. How may I assist you?",
        "thinking": "Processing your request.",
        "error": "I didn't understand that. Could you repeat?",
        "script_pattern": r"[a-zA-Z]",
    },
    "ml": {
        "name": "Malayalam",
        "native": "മലയാളം",
        "whisper_code": "ml",
        "google_tts_code": "ml-IN",
        "gtts_code": "ml",
        "indic_code": "mal_Mlym",
        "wake_words": ["ജാർവിസ്", "ഹേ ജാർവിസ്", "jarvis"],
        "greeting": "നമസ്കാരം. ഞാൻ ജാർവിസ് ആണ്. എങ്ങനെ സഹായിക്കാൻ കഴിയും?",
        "thinking": "ഒരു നിമിഷം...",
        "error": "മനസ്സിലായില്ല. ദയവായി ആവർത്തിക്കൂ.",
        "script_pattern": r"[\u0D00-\u0D7F]",   # Malayalam Unicode block
    },
    "hi": {
        "name": "Hindi",
        "native": "हिंदी",
        "whisper_code": "hi",
        "google_tts_code": "hi-IN",
        "gtts_code": "hi",
        "indic_code": "hin_Deva",
        "wake_words": ["जार्विस", "हे जार्विस", "jarvis"],
        "greeting": "नमस्ते! मैं जार्विस हूँ। आप की क्या सेवा कर सकता हूँ?",
        "thinking": "एक पल...",
        "error": "मुझे समझ नहीं आया। कृपया दोबारा कहें।",
        "script_pattern": r"[\u0900-\u097F]",   # Devanagari Unicode block
    },
    "ta": {
        "name": "Tamil",
        "native": "தமிழ்",
        "whisper_code": "ta",
        "google_tts_code": "ta-IN",
        "gtts_code": "ta",
        "indic_code": "tam_Taml",
        "wake_words": ["ஜார்விஸ்", "ஹே ஜார்விஸ்", "jarvis"],
        "greeting": "வணக்கம்! நான் ஜார்விஸ். உங்களுக்கு எப்படி உதவலாம்?",
        "thinking": "ஒரு நிமிடம்...",
        "error": "புரியவில்லை. மீண்டும் சொல்லுங்கள்.",
        "script_pattern": r"[\u0B80-\u0BFF]",   # Tamil Unicode block
    },
}

DEFAULT_LANGUAGE = "en"


# ─────────────────────────────────────────────
# Language detector
# ─────────────────────────────────────────────

class LanguageDetector:
    """
    Detect language from text using script analysis + AI detection.
    Script detection is instant and works offline.
    """

    SCRIPT_PATTERNS = {
        "ml": re.compile(r"[\u0D00-\u0D7F]"),
        "hi": re.compile(r"[\u0900-\u097F]"),
        "ta": re.compile(r"[\u0B80-\u0BFF]"),
        "te": re.compile(r"[\u0C00-\u0C7F]"),   # Telugu (bonus)
        "kn": re.compile(r"[\u0C80-\u0CFF]"),   # Kannada (bonus)
    }

    def __init__(self):
        self._lib_available = False
        try:
            from langdetect import detect
            self._detect = detect
            self._lib_available = True
        except ImportError:
            pass

    def detect_from_script(self, text: str) -> Optional[str]:
        """Instant script-based detection — works offline, very accurate for Indian scripts."""
        if not text:
            return None
        for lang, pattern in self.SCRIPT_PATTERNS.items():
            if pattern.search(text):
                return lang
        return None

    def detect_from_library(self, text: str) -> Optional[str]:
        """Use langdetect library for Latin-script languages."""
        if not self._lib_available or not text:
            return None
        try:
            detected = self._detect(text)
            # Map to our supported codes
            mapping = {"en": "en", "ml": "ml", "hi": "hi", "ta": "ta",
                       "mr": "hi", "ne": "hi"}  # Marathi/Nepali → Hindi fallback
            return mapping.get(detected, "en")
        except Exception:
            return None

    def detect(self, text: str) -> str:
        """Best-effort language detection. Returns language code."""
        if not text or not text.strip():
            return DEFAULT_LANGUAGE

        # 1. Script analysis (instant, very reliable for non-Latin scripts)
        lang = self.detect_from_script(text)
        if lang and lang in SUPPORTED_LANGUAGES:
            return lang

        # 2. Library detection (for English vs romanized Indic text)
        lang = self.detect_from_library(text)
        if lang and lang in SUPPORTED_LANGUAGES:
            return lang

        # 3. Keyword-based detection for romanized text
        text_lower = text.lower()
        malayalam_roman = ["ente", "njan", "ningal", "sheriyanu", "vendaa", "poda", "mone", "mol"]
        hindi_roman = ["main", "aap", "kya", "nahi", "haan", "theek", "achha", "bhai"]
        tamil_roman = ["naan", "neengal", "enna", "illai", "aamaa", "romba", "vanakkam"]

        ml_score = sum(1 for w in malayalam_roman if w in text_lower)
        hi_score = sum(1 for w in hindi_roman if w in text_lower)
        ta_score = sum(1 for w in tamil_roman if w in text_lower)

        if max(ml_score, hi_score, ta_score) > 0:
            return max(zip([ml_score, hi_score, ta_score], ["ml", "hi", "ta"]))[1]

        return "en"


# ─────────────────────────────────────────────
# Translation providers
# ─────────────────────────────────────────────

class GoogleTranslateProvider:
    """Google Cloud Translate API (paid, most accurate)."""
    name = "google_cloud"

    def __init__(self):
        self.api_key = os.getenv("GOOGLE_TRANSLATE_API_KEY", "")
        self.enabled = bool(self.api_key)

    async def translate(self, text: str, target: str, source: str = "auto") -> Optional[str]:
        if not self.enabled:
            return None
        try:
            import aiohttp
            url = f"https://translation.googleapis.com/language/translate/v2?key={self.api_key}"
            payload = {"q": text, "target": target, "format": "text"}
            if source != "auto":
                payload["source"] = source
            async with aiohttp.ClientSession() as s:
                async with s.post(url, json=payload) as r:
                    data = await r.json()
                    return data["data"]["translations"][0]["translatedText"]
        except Exception as e:
            logger.warning(f"Google Translate error: {e}")
            return None


class DeepTranslatorProvider:
    """deep-translator — free Google Translate wrapper, no API key."""
    name = "deep_translator"

    def __init__(self):
        self.enabled = False
        try:
            from deep_translator import GoogleTranslator
            self._cls = GoogleTranslator
            self.enabled = True
        except ImportError:
            pass

    async def translate(self, text: str, target: str, source: str = "auto") -> Optional[str]:
        if not self.enabled:
            return None
        try:
            translator = self._cls(source=source, target=target)
            result = await asyncio.to_thread(translator.translate, text)
            return result
        except Exception as e:
            logger.warning(f"deep-translator error: {e}")
            return None


class LibreTranslateProvider:
    """LibreTranslate — self-hosted or public instance, free."""
    name = "libretranslate"

    def __init__(self, url: str = None):
        self.url = url or os.getenv("LIBRETRANSLATE_URL", "https://libretranslate.com")
        self.api_key = os.getenv("LIBRETRANSLATE_API_KEY", "")
        self.enabled = True

    async def translate(self, text: str, target: str, source: str = "auto") -> Optional[str]:
        try:
            import aiohttp
            payload = {"q": text, "source": source if source != "auto" else "en",
                       "target": target, "format": "text"}
            if self.api_key:
                payload["api_key"] = self.api_key
            async with aiohttp.ClientSession() as s:
                async with s.post(f"{self.url}/translate", json=payload,
                                  timeout=aiohttp.ClientTimeout(total=10)) as r:
                    if r.status == 200:
                        data = await r.json()
                        return data.get("translatedText")
        except Exception as e:
            logger.warning(f"LibreTranslate error: {e}")
            return None


class ArgosTranslateProvider:
    """Argos Translate — fully offline, no internet required."""
    name = "argos"

    def __init__(self):
        self.enabled = False
        self._installed_pairs = set()
        try:
            import argostranslate.package
            import argostranslate.translate
            self._argos = argostranslate
            self.enabled = True
            self._scan_installed()
        except ImportError:
            pass

    def _scan_installed(self):
        try:
            for pkg in self._argos.package.get_installed_packages():
                self._installed_pairs.add((pkg.from_code, pkg.to_code))
        except Exception:
            pass

    async def ensure_pair(self, src: str, tgt: str):
        """Download language pair if not installed."""
        if (src, tgt) in self._installed_pairs:
            return True
        try:
            await asyncio.to_thread(self._argos.package.update_package_index)
            pkgs = self._argos.package.get_available_packages()
            pkg = next((p for p in pkgs if p.from_code == src and p.to_code == tgt), None)
            if pkg:
                await asyncio.to_thread(pkg.install)
                self._installed_pairs.add((src, tgt))
                return True
        except Exception as e:
            logger.warning(f"Argos package install failed: {e}")
        return False

    async def translate(self, text: str, target: str, source: str = "en") -> Optional[str]:
        if not self.enabled:
            return None
        src = source if source != "auto" else "en"
        try:
            await self.ensure_pair(src, target)
            result = await asyncio.to_thread(
                self._argos.translate.translate, text, src, target
            )
            return result
        except Exception as e:
            logger.warning(f"Argos translate error: {e}")
            return None


# ─────────────────────────────────────────────
# Multilingual TTS voice mapping
# ─────────────────────────────────────────────

VOICE_MAP = {
    # ElevenLabs voice IDs
    "elevenlabs": {
        "en": "pNInz6obpgDQGcFmaJgB",   # Adam — deep, formal
        "ml": "21m00Tcm4TlvDq8ikWAM",   # Rachel (closest to Indian English)
        "hi": "21m00Tcm4TlvDq8ikWAM",
        "ta": "21m00Tcm4TlvDq8ikWAM",
    },
    # Google TTS voice names
    "google": {
        "en": "en-IN-Neural2-C",         # Indian English male
        "ml": "ml-IN-Wavenet-A",         # Malayalam female
        "hi": "hi-IN-Neural2-B",         # Hindi male
        "ta": "ta-IN-Neural2-A",         # Tamil female
    },
    # gTTS language codes
    "gtts": {
        "en": "en",
        "ml": "ml",
        "hi": "hi",
        "ta": "ta",
    },
    # pyttsx3 — use system voice (limited Indic support)
    "pyttsx3": {
        "en": 0,    # voice index
        "ml": 0,
        "hi": 0,
        "ta": 0,
    },
}


# ─────────────────────────────────────────────
# Language Manager (main class)
# ─────────────────────────────────────────────

@dataclass
class TranslationResult:
    text: str
    source_lang: str
    target_lang: str
    provider: str
    success: bool = True
    error: Optional[str] = None


class LanguageManager:
    """
    JARVIS Language Manager.
    Handles: detection → translation → response translation → voice routing.
    """

    def __init__(self, config: dict = None):
        cfg = config or {}
        self.default_lang = cfg.get("language", DEFAULT_LANGUAGE)
        self.current_lang = self.default_lang
        self.auto_detect = cfg.get("auto_detect", True)

        self.detector = LanguageDetector()

        # Build translation provider chain
        self._translators = []
        self._translators.append(GoogleTranslateProvider())
        self._translators.append(DeepTranslatorProvider())
        self._translators.append(LibreTranslateProvider())
        self._translators.append(ArgosTranslateProvider())

        available = [t.name for t in self._translators if t.enabled]
        logger.info(f"Language Manager ready | translators: {available} | default: {self.default_lang}")

    # ── Core API ────────────────────────────────────────────

    def detect(self, text: str) -> str:
        """Detect language of input text."""
        if not self.auto_detect:
            return self.current_lang
        lang = self.detector.detect(text)
        return lang

    async def translate(self, text: str, target: str, source: str = "auto") -> TranslationResult:
        """Translate text to target language using best available provider."""
        if not text.strip():
            return TranslationResult(text, source, target, "passthrough")

        # Same language — skip translation
        detected = source if source != "auto" else self.detect(text)
        if detected == target:
            return TranslationResult(text, detected, target, "passthrough")

        for translator in self._translators:
            if not translator.enabled:
                continue
            try:
                result = await translator.translate(text, target, source)
                if result:
                    logger.debug(f"[{translator.name}] {detected}→{target}: '{text[:30]}' → '{result[:30]}'")
                    return TranslationResult(result, detected, target, translator.name)
            except Exception as e:
                logger.warning(f"Translator {translator.name} failed: {e}")

        # All failed — return original
        logger.warning(f"All translators failed for {detected}→{target}. Returning original.")
        return TranslationResult(text, detected, target, "none", success=False,
                                 error="All translators failed")

    async def to_english(self, text: str) -> tuple[str, str]:
        """
        Translate any supported language → English.
        Returns (english_text, detected_language_code).
        """
        lang = self.detect(text)
        if lang == "en":
            return text, "en"
        result = await self.translate(text, "en", lang)
        return result.text, lang

    async def from_english(self, text: str, target_lang: str) -> str:
        """Translate English response → target language."""
        if target_lang == "en":
            return text
        result = await self.translate(text, target_lang, "en")
        return result.text

    # ── Convenience ─────────────────────────────────────────

    def get_lang_info(self, code: str = None) -> dict:
        code = code or self.current_lang
        return SUPPORTED_LANGUAGES.get(code, SUPPORTED_LANGUAGES["en"])

    def set_language(self, code: str):
        if code in SUPPORTED_LANGUAGES:
            self.current_lang = code
            logger.info(f"Language set to: {SUPPORTED_LANGUAGES[code]['name']}")
        else:
            logger.warning(f"Unsupported language: {code}")

    def get_wake_words(self) -> list[str]:
        """All wake words across all supported languages."""
        words = []
        for lang in SUPPORTED_LANGUAGES.values():
            words.extend(lang["wake_words"])
        return list(set(words))

    def get_greeting(self, lang: str = None) -> str:
        lang = lang or self.current_lang
        return SUPPORTED_LANGUAGES.get(lang, SUPPORTED_LANGUAGES["en"])["greeting"]

    def get_thinking_phrase(self, lang: str = None) -> str:
        lang = lang or self.current_lang
        return SUPPORTED_LANGUAGES.get(lang, SUPPORTED_LANGUAGES["en"])["thinking"]

    def get_error_phrase(self, lang: str = None) -> str:
        lang = lang or self.current_lang
        return SUPPORTED_LANGUAGES.get(lang, SUPPORTED_LANGUAGES["en"])["error"]

    def get_tts_config(self, provider: str, lang: str = None) -> str | int:
        """Get the correct voice ID/code for this language and TTS provider."""
        lang = lang or self.current_lang
        return VOICE_MAP.get(provider, {}).get(lang, VOICE_MAP.get(provider, {}).get("en"))

    def list_languages(self) -> list[dict]:
        return [
            {"code": code, "name": info["name"], "native": info["native"]}
            for code, info in SUPPORTED_LANGUAGES.items()
        ]

    def is_supported(self, lang_code: str) -> bool:
        return lang_code in SUPPORTED_LANGUAGES
