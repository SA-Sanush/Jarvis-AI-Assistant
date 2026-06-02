"""
JARVIS Multilingual STT — voice/multilingual_stt.py
Language-aware speech-to-text.
Whisper auto-detects language OR can be forced to a specific language.
After transcription, detected language is returned for routing.
"""

import asyncio
import logging
import numpy as np
from typing import Optional
from dataclasses import dataclass

from .stt import STT, STTResult, AudioRecorder
from core.language import LanguageDetector, SUPPORTED_LANGUAGES

logger = logging.getLogger("jarvis.stt.multilingual")


@dataclass
class MultilingualSTTResult:
    text: str
    text_english: str           # Always-English version (for AI brain)
    detected_lang: str          # "en" | "ml" | "hi" | "ta"
    original_text: str          # Raw transcription before translation
    confidence: float
    provider: str
    latency_ms: float
    success: bool = True
    error: Optional[str] = None


class MultilingualSTT:
    """
    Multilingual speech-to-text wrapper.
    Uses Whisper's built-in multilingual support — it auto-detects
    English, Malayalam, Hindi, Tamil and many more.
    """

    def __init__(self, config: dict = None, language_manager=None):
        cfg = config or {}
        self.lm = language_manager
        self.detector = LanguageDetector()
        self.recorder = AudioRecorder()

        # Force language or auto-detect
        self.force_lang = cfg.get("force_language", None)   # e.g. "ml" to force Malayalam

        # Build Whisper STT — always multilingual model
        whisper_cfg = cfg.get("whisper", {})
        # Make sure we use a multilingual model (not ".en" suffix models)
        model = whisper_cfg.get("model_size", "small")
        if model.endswith(".en"):
            model = model.replace(".en", "")   # .en models are English-only
            whisper_cfg["model_size"] = model

        self._stt = STT({"stt_provider": "whisper", "whisper": whisper_cfg})
        logger.info(f"Multilingual STT ready | model: {model} | force_lang: {self.force_lang or 'auto'}")

    async def listen(self) -> MultilingualSTTResult:
        """Record from mic until silence, then transcribe with language detection."""
        audio = await self.recorder.record_until_silence()
        if len(audio) < 1000:
            return MultilingualSTTResult(
                text="", text_english="", detected_lang="en",
                original_text="", confidence=0.0, provider="none",
                latency_ms=0, success=False, error="No audio captured"
            )
        return await self.transcribe(audio)

    async def transcribe(self, audio: np.ndarray, sample_rate: int = 16000) -> MultilingualSTTResult:
        """Transcribe audio with automatic language detection."""
        import time
        t0 = time.time()

        # Get base transcription
        result = await self._stt.transcribe(audio, sample_rate)
        if not result.success:
            return MultilingualSTTResult(
                text="", text_english="", detected_lang="en",
                original_text="", confidence=0.0, provider=result.provider,
                latency_ms=(time.time() - t0) * 1000, success=False, error=result.error
            )

        raw_text = result.text.strip()
        detected_lang = result.language or "en"

        # Whisper returns ISO codes — map to our codes
        lang_map = {
            "malayalam": "ml", "ml": "ml",
            "hindi": "hi", "hi": "hi",
            "tamil": "ta", "ta": "ta",
            "english": "en", "en": "en",
            "telugu": "te", "kannada": "kn",
        }
        detected_lang = lang_map.get(detected_lang, detected_lang)

        # Override with script detection if Whisper missed it
        script_lang = self.detector.detect_from_script(raw_text)
        if script_lang and script_lang in SUPPORTED_LANGUAGES:
            detected_lang = script_lang

        # Apply forced language
        if self.force_lang:
            detected_lang = self.force_lang

        # If not supported, fall back to English
        if detected_lang not in SUPPORTED_LANGUAGES:
            detected_lang = "en"

        # Update language manager if present
        if self.lm:
            self.lm.current_lang = detected_lang

        # Translate to English for AI brain (if not already English)
        text_english = raw_text
        if detected_lang != "en" and self.lm:
            translation = await self.lm.translate(raw_text, "en", detected_lang)
            text_english = translation.text
            logger.info(f"[STT] {detected_lang}: '{raw_text}' → EN: '{text_english}'")
        else:
            logger.info(f"[STT] en: '{raw_text}'")

        return MultilingualSTTResult(
            text=raw_text,
            text_english=text_english,
            detected_lang=detected_lang,
            original_text=raw_text,
            confidence=result.confidence,
            provider=result.provider,
            latency_ms=(time.time() - t0) * 1000
        )
