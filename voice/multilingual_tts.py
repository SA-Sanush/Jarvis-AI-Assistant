"""
JARVIS Multilingual TTS — voice/multilingual_tts.py
Language-aware text-to-speech.

For each language, picks the best available voice:
  English  → Coqui / ElevenLabs Adam / Google en-IN / pyttsx3
  Malayalam→ Google ml-IN-Wavenet / gTTS ml / espeak ml
  Hindi    → Google hi-IN-Neural2 / gTTS hi / pyttsx3
  Tamil    → Google ta-IN-Neural2 / gTTS ta / pyttsx3

Falls back gracefully when a language-specific voice is unavailable.
"""

import os
import asyncio
import logging
import tempfile
from typing import Optional

from core.language import SUPPORTED_LANGUAGES, VOICE_MAP, LanguageManager

logger = logging.getLogger("jarvis.tts.multilingual")


class MultilingualTTS:
    """
    Language-aware TTS that picks the right voice/engine per language.
    """

    def __init__(self, config: dict = None, language_manager: LanguageManager = None):
        cfg = config or {}
        self.lm = language_manager
        self.preferred = cfg.get("tts_provider", "gtts")   # Default fallback

        # ElevenLabs config
        self._el_key = os.getenv("ELEVENLABS_API_KEY", "") or cfg.get("elevenlabs", {}).get("api_key", "")

        # Google TTS config
        self._google_key = os.getenv("GOOGLE_API_KEY", "") or cfg.get("google_tts", {}).get("api_key", "")

        logger.info(f"Multilingual TTS ready | ElevenLabs: {bool(self._el_key)} | Google: {bool(self._google_key)}")

    async def speak(self, text: str, lang: str = None) -> bool:
        """Speak text in the given language using the best available engine."""
        if not text.strip():
            return True

        lang = lang or (self.lm.current_lang if self.lm else "en")
        lang_info = SUPPORTED_LANGUAGES.get(lang, SUPPORTED_LANGUAGES["en"])

        logger.info(f"TTS [{lang}]: {text[:60]}...")

        # Try providers in order of quality
        providers = self._get_provider_order(lang)
        for provider_name in providers:
            try:
                success = await self._speak_with(text, lang, provider_name, lang_info)
                if success:
                    return True
            except Exception as e:
                logger.warning(f"TTS [{provider_name}] failed: {e}")

        logger.error(f"All TTS providers failed for lang={lang}")
        return False

    def _get_provider_order(self, lang: str) -> list[str]:
        """Return preferred TTS provider order for this language."""
        if lang == "en":
            # English: best quality options
            order = []
            if self._el_key:
                order.append("elevenlabs")
            if self._google_key:
                order.append("google")
            order += ["coqui", "gtts", "pyttsx3"]
            return order
        else:
            # Indian languages: Google best, gTTS good, pyttsx3 last
            order = []
            if self._google_key:
                order.append("google")
            order += ["gtts", "espeak", "pyttsx3"]
            return order

    async def _speak_with(self, text: str, lang: str, provider: str, lang_info: dict) -> bool:
        """Speak using a specific provider."""

        if provider == "elevenlabs":
            return await self._speak_elevenlabs(text, lang)

        if provider == "google":
            return await self._speak_google_tts(text, lang_info["google_tts_code"])

        if provider == "gtts":
            return await self._speak_gtts(text, lang_info["gtts_code"])

        if provider == "coqui":
            return await self._speak_coqui(text)

        if provider == "espeak":
            return await self._speak_espeak(text, lang)

        if provider == "pyttsx3":
            return await self._speak_pyttsx3(text)

        return False

    # ── Provider implementations ────────────────────────────

    async def _speak_gtts(self, text: str, lang_code: str) -> bool:
        """gTTS — best free option for Indian languages."""
        try:
            from gtts import gTTS

            tts = gTTS(text=text, lang=lang_code, slow=False)
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                path = f.name
            await asyncio.to_thread(tts.save, path)
            await _play_audio(path)
            return True
        except ImportError:
            logger.debug("gtts not installed")
            return False
        except Exception as e:
            logger.warning(f"gTTS error: {e}")
            return False

    async def _speak_google_tts(self, text: str, voice_code: str) -> bool:
        """Google Cloud TTS — best quality for Indian languages."""
        try:
            from google.cloud import texttospeech
            client = texttospeech.TextToSpeechAsyncClient()

            lang_code = "-".join(voice_code.split("-")[:2])   # "ml-IN-Wavenet-A" → "ml-IN"
            synthesis_input = texttospeech.SynthesisInput(text=text)
            voice = texttospeech.VoiceSelectionParams(
                language_code=lang_code,
                name=voice_code
            )
            audio_config = texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.MP3,
                speaking_rate=1.05
            )
            response = await client.synthesize_speech(
                input=synthesis_input, voice=voice, audio_config=audio_config
            )
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                f.write(response.audio_content)
                path = f.name
            await _play_audio(path)
            return True
        except ImportError:
            return False
        except Exception as e:
            logger.warning(f"Google Cloud TTS error: {e}")
            return False

    async def _speak_elevenlabs(self, text: str, lang: str) -> bool:
        """ElevenLabs — best for English, usable for others."""
        try:
            from elevenlabs.client import AsyncElevenLabs
            from elevenlabs import VoiceSettings
            voice_id = VOICE_MAP["elevenlabs"].get(lang, VOICE_MAP["elevenlabs"]["en"])
            client = AsyncElevenLabs(api_key=self._el_key)
            audio_gen = await client.text_to_speech.convert(
                voice_id=voice_id,
                text=text,
                model_id="eleven_turbo_v2_5",
                output_format="mp3_22050_32"
            )
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                path = f.name
                async for chunk in audio_gen:
                    f.write(chunk)
            await _play_audio(path)
            return True
        except ImportError:
            return False
        except Exception as e:
            logger.warning(f"ElevenLabs error: {e}")
            return False

    async def _speak_coqui(self, text: str) -> bool:
        """Coqui TTS — good local English."""
        try:
            from TTS.api import TTS
            import threading
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                path = f.name
            tts = TTS("tts_models/en/ljspeech/tacotron2-DDC")
            await asyncio.to_thread(tts.tts_to_file, text=text, file_path=path)
            await _play_audio(path)
            return True
        except ImportError:
            return False
        except Exception as e:
            logger.warning(f"Coqui TTS error: {e}")
            return False

    async def _speak_espeak(self, text: str, lang: str) -> bool:
        """espeak-ng — free, works on Linux for Indic languages."""
        espeak_langs = {"ml": "ml", "hi": "hi", "ta": "ta", "en": "en"}
        code = espeak_langs.get(lang, "en")
        try:
            import subprocess
            result = await asyncio.create_subprocess_exec(
                "espeak-ng", "-v", code, text,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await result.wait()
            return result.returncode == 0
        except FileNotFoundError:
            logger.debug("espeak-ng not found")
            return False

    async def _speak_pyttsx3(self, text: str) -> bool:
        """pyttsx3 — system voice fallback."""
        try:
            import pyttsx3
            import threading

            def _speak():
                engine = pyttsx3.init()
                engine.setProperty("rate", 170)
                engine.say(text)
                engine.runAndWait()

            await asyncio.to_thread(_speak)
            return True
        except ImportError:
            return False
        except Exception as e:
            logger.warning(f"pyttsx3 error: {e}")
            return False

    async def speak_streaming(self, text_iter, lang: str = None):
        """Speak sentence by sentence from a streaming token iterator."""
        lang = lang or (self.lm.current_lang if self.lm else "en")
        buffer = ""
        sentence_ends = {".", "!", "?", "।", "॥", ".", "!", "?"}  # includes Devanagari danda

        async for token in text_iter:
            buffer += token
            if (any(buffer.rstrip().endswith(e) for e in sentence_ends)
                    and len(buffer.strip()) > 20):
                await self.speak(buffer.strip(), lang)
                buffer = ""

        if buffer.strip():
            await self.speak(buffer.strip(), lang)


# ── Audio playback helper ──────────────────────────────────

async def _play_audio(path: str):
    """Play audio file using sounddevice or system player."""
    import platform
    OS = platform.system()
    try:
        import soundfile as sf
        import sounddevice as sd
        data, sr = await asyncio.to_thread(sf.read, path, dtype="float32")
        await asyncio.to_thread(sd.play, data, sr)
        await asyncio.to_thread(sd.wait)
    except Exception:
        if OS == "Windows":
            import subprocess
            subprocess.Popen(["powershell", "-c",
                              f"(New-Object Media.SoundPlayer '{path}').PlaySync()"])
        else:
            proc = await asyncio.create_subprocess_exec(
                "ffplay", "-nodisp", "-autoexit", path,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await proc.wait()
