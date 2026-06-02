"""
JARVIS Text-to-Speech — voice/tts.py
Converts text to spoken audio using JARVIS's voice.
Primary:  Coqui TTS  — local, free, high quality, JARVIS-like voice
Fallback: ElevenLabs — best cloud quality, very natural
Fallback: Google TTS — reliable cloud
Fallback: pyttsx3    — offline, system voice, zero dependencies
"""

import os
import io
import time
import logging
import asyncio
import platform
import tempfile
import threading
from dataclasses import dataclass
from typing import Optional
from pathlib import Path

logger = logging.getLogger("jarvis.tts")
OS = platform.system()


@dataclass
class TTSResult:
    audio_path: Optional[str]   # Path to audio file, or None if played directly
    provider: str
    latency_ms: float
    success: bool = True
    error: Optional[str] = None


# ─────────────────────────────────────────────
# Backend: Coqui TTS (local, free)
# ─────────────────────────────────────────────

class CoquiTTS:
    """
    Best local TTS. Runs fully offline.
    Model: tts_models/en/ljspeech/tacotron2-DDC (classic, JARVIS-like)
    Or:    tts_models/en/vctk/vits (multi-speaker)
    """
    name = "coqui"

    def __init__(self, cfg: dict):
        self.model = cfg.get("model", "tts_models/en/ljspeech/tacotron2-DDC")
        self.speaker = cfg.get("speaker", None)
        self.speed = cfg.get("speed", 1.1)         # Slightly faster = more crisp
        self._tts = None
        self._available = False
        self._lock = threading.Lock()
        self._init()

    def _init(self):
        try:
            from TTS.api import TTS
            import torch
            use_gpu = torch.cuda.is_available()
            self._tts = TTS(self.model, gpu=use_gpu)
            self._available = True
            logger.info(f"✅ Coqui TTS ready ({self.model}, GPU={use_gpu})")
        except ImportError:
            logger.debug("TTS not installed: pip install TTS")
        except Exception as e:
            logger.warning(f"Coqui TTS init failed: {e}")

    @property
    def available(self) -> bool:
        return self._available

    async def speak(self, text: str) -> TTSResult:
        t0 = time.time()
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                out_path = f.name

            with self._lock:
                kwargs = {"text": text, "file_path": out_path}
                if self.speaker:
                    kwargs["speaker"] = self.speaker
                await asyncio.to_thread(self._tts.tts_to_file, **kwargs)

            await _play_audio(out_path)
            latency = (time.time() - t0) * 1000
            return TTSResult(out_path, self.name, latency)
        except Exception as e:
            return TTSResult(None, self.name, 0, False, str(e))


# ─────────────────────────────────────────────
# Backend: ElevenLabs (cloud, best quality)
# ─────────────────────────────────────────────

class ElevenLabsTTS:
    """
    Best cloud TTS. Very natural sounding.
    Free tier: 10,000 chars/month
    Voice IDs: https://api.elevenlabs.io/v1/voices
    """
    name = "elevenlabs"

    JARVIS_VOICE_SETTINGS = {
        "stability": 0.75,
        "similarity_boost": 0.75,
        "style": 0.0,
        "use_speaker_boost": True
    }

    def __init__(self, cfg: dict):
        self.api_key = cfg.get("api_key") or os.getenv("ELEVENLABS_API_KEY", "")
        # Default to "Adam" - deep, formal, JARVIS-like
        self.voice_id = cfg.get("voice_id", "pNInz6obpgDQGcFmaJgB")
        self.model = cfg.get("model", "eleven_turbo_v2_5")  # Fastest
        self._available = bool(self.api_key)

    @property
    def available(self) -> bool:
        return self._available

    async def speak(self, text: str) -> TTSResult:
        t0 = time.time()
        try:
            from elevenlabs.client import AsyncElevenLabs
            from elevenlabs import VoiceSettings
            client = AsyncElevenLabs(api_key=self.api_key)

            audio_gen = await client.text_to_speech.convert(
                voice_id=self.voice_id,
                text=text,
                model_id=self.model,
                voice_settings=VoiceSettings(**self.JARVIS_VOICE_SETTINGS),
                output_format="mp3_22050_32"
            )

            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                out_path = f.name
                async for chunk in audio_gen:
                    f.write(chunk)

            await _play_audio(out_path)
            return TTSResult(out_path, self.name, (time.time() - t0) * 1000)
        except Exception as e:
            return TTSResult(None, self.name, 0, False, str(e))


# ─────────────────────────────────────────────
# Backend: Google TTS (cloud, free tier)
# ─────────────────────────────────────────────

class GoogleTTS:
    """Google Text-to-Speech — good quality, free tier available."""
    name = "google"

    def __init__(self, cfg: dict):
        self.api_key = cfg.get("api_key") or os.getenv("GOOGLE_API_KEY", "")
        self.language = cfg.get("language", "en-US")
        self.voice_name = cfg.get("voice_name", "en-US-Neural2-D")  # Male, natural
        self.speaking_rate = cfg.get("speaking_rate", 1.1)
        self._available = bool(self.api_key)

    @property
    def available(self) -> bool:
        return self._available

    async def speak(self, text: str) -> TTSResult:
        t0 = time.time()
        try:
            from google.cloud import texttospeech
            client = texttospeech.TextToSpeechAsyncClient()
            synthesis_input = texttospeech.SynthesisInput(text=text)
            voice = texttospeech.VoiceSelectionParams(
                language_code=self.language, name=self.voice_name
            )
            audio_config = texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.MP3,
                speaking_rate=self.speaking_rate,
                pitch=-2.0   # Slightly deeper = more JARVIS-like
            )
            response = await client.synthesize_speech(
                input=synthesis_input, voice=voice, audio_config=audio_config
            )
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                f.write(response.audio_content)
                out_path = f.name
            await _play_audio(out_path)
            return TTSResult(out_path, self.name, (time.time() - t0) * 1000)
        except Exception as e:
            return TTSResult(None, self.name, 0, False, str(e))


# ─────────────────────────────────────────────
# Backend: gTTS (free, no API key)
# ─────────────────────────────────────────────

class GTTSProvider:
    """Google Translate TTS — completely free, no API key."""
    name = "gtts"

    def __init__(self):
        self._available = False
        try:
            import gtts
            self._available = True
        except ImportError:
            pass

    @property
    def available(self) -> bool:
        return self._available

    async def speak(self, text: str) -> TTSResult:
        t0 = time.time()
        try:
            from gtts import gTTS
            tts = gTTS(text=text, lang="en", slow=False)
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                out_path = f.name
            await asyncio.to_thread(tts.save, out_path)
            await _play_audio(out_path)
            return TTSResult(out_path, self.name, (time.time() - t0) * 1000)
        except Exception as e:
            return TTSResult(None, self.name, 0, False, str(e))


# ─────────────────────────────────────────────
# Backend: pyttsx3 (fully offline, system voice)
# ─────────────────────────────────────────────

class Pyttsx3TTS:
    """
    Zero-dependency offline TTS using system voices.
    Windows: SAPI5 | Linux: espeak | macOS: NSSpeechSynthesizer
    """
    name = "pyttsx3"

    def __init__(self, cfg: dict):
        self.rate = cfg.get("rate", 175)      # Words per minute (default ~150)
        self.volume = cfg.get("volume", 0.9)
        self.voice_index = cfg.get("voice_index", 0)  # 0=first, 1=second, etc.
        self._engine = None
        self._lock = threading.Lock()
        self._available = False
        self._init()

    def _init(self):
        try:
            import pyttsx3
            self._engine = pyttsx3.init()
            self._engine.setProperty("rate", self.rate)
            self._engine.setProperty("volume", self.volume)
            voices = self._engine.getProperty("voices")
            if voices and len(voices) > self.voice_index:
                self._engine.setProperty("voice", voices[self.voice_index].id)
            self._available = True
            logger.info("✅ pyttsx3 TTS ready (offline)")
        except ImportError:
            logger.debug("pyttsx3 not installed")
        except Exception as e:
            logger.warning(f"pyttsx3 init failed: {e}")

    @property
    def available(self) -> bool:
        return self._available

    async def speak(self, text: str) -> TTSResult:
        t0 = time.time()
        try:
            with self._lock:
                await asyncio.to_thread(self._engine.say, text)
                await asyncio.to_thread(self._engine.runAndWait)
            return TTSResult(None, self.name, (time.time() - t0) * 1000)
        except Exception as e:
            return TTSResult(None, self.name, 0, False, str(e))


# ─────────────────────────────────────────────
# Audio playback helper
# ─────────────────────────────────────────────

async def _play_audio(path: str):
    """Play an audio file cross-platform."""
    try:
        import soundfile as sf
        import sounddevice as sd
        data, sr = await asyncio.to_thread(sf.read, path, dtype="float32")
        await asyncio.to_thread(sd.play, data, sr)
        await asyncio.to_thread(sd.wait)
    except Exception:
        # Fallback: system player
        if OS == "Windows":
            import subprocess
            subprocess.Popen(["powershell", "-c", f"(New-Object Media.SoundPlayer '{path}').PlaySync()"])
        elif OS == "Linux":
            await asyncio.to_thread(os.system, f"ffplay -nodisp -autoexit '{path}' 2>/dev/null")


# ─────────────────────────────────────────────
# TTS Manager
# ─────────────────────────────────────────────

class TTS:
    """
    JARVIS Text-to-Speech manager.
    Priority: coqui (local) → elevenlabs → google → gtts → pyttsx3
    """

    # JARVIS response prefixes for immersion
    THINKING_SOUNDS = ["Processing...", "One moment.", "Right away."]

    def __init__(self, config: dict = None):
        cfg = config or {}
        preferred = cfg.get("tts_provider", "coqui")

        all_providers = {
            "coqui":      CoquiTTS(cfg.get("coqui", {})),
            "elevenlabs": ElevenLabsTTS(cfg.get("elevenlabs", {})),
            "google":     GoogleTTS(cfg.get("google_tts", {})),
            "gtts":       GTTSProvider(),
            "pyttsx3":    Pyttsx3TTS(cfg.get("pyttsx3", {})),
        }

        order = [preferred] + [k for k in all_providers if k != preferred]
        self.providers = {k: all_providers[k] for k in order if all_providers[k].available}

        if not self.providers:
            logger.error("No TTS providers available!")
        else:
            logger.info(f"TTS providers: {list(self.providers.keys())}")

        self._speaking = False
        self._speech_lock = asyncio.Lock()

    async def speak(self, text: str, interrupt: bool = False) -> TTSResult:
        """
        Speak text aloud. Handles queuing and interruption.
        """
        if not text.strip():
            return TTSResult(None, "none", 0, False, "Empty text")

        # Clean text for speech (remove markdown, URLs, etc.)
        clean = _clean_for_speech(text)

        async with self._speech_lock:
            self._speaking = True
            for name, provider in self.providers.items():
                result = await provider.speak(clean)
                if result.success:
                    logger.info(f"[{name}] Spoke {len(clean)} chars in {result.latency_ms:.0f}ms")
                    self._speaking = False
                    return result
                else:
                    logger.warning(f"[{name}] TTS failed: {result.error}")
            self._speaking = False

        return TTSResult(None, "none", 0, False, "All TTS providers failed")

    async def speak_streaming(self, text_iter):
        """
        Speak as tokens stream in — starts speaking before full response is ready.
        Buffers into sentences for natural-sounding output.
        """
        buffer = ""
        sentence_ends = {".", "!", "?", ":", ";"}

        async for token in text_iter:
            buffer += token
            # Speak when we have a complete sentence
            if any(buffer.rstrip().endswith(e) for e in sentence_ends) and len(buffer) > 20:
                await self.speak(buffer.strip())
                buffer = ""

        # Speak any remaining text
        if buffer.strip():
            await self.speak(buffer.strip())

    @property
    def is_speaking(self) -> bool:
        return self._speaking

    def list_providers(self) -> list[str]:
        return list(self.providers.keys())


def _clean_for_speech(text: str) -> str:
    """Strip markdown and formatting from text before speaking."""
    import re
    # Remove markdown
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)   # bold
    text = re.sub(r"\*(.+?)\*", r"\1", text)         # italic
    text = re.sub(r"`(.+?)`", r"\1", text)            # inline code
    text = re.sub(r"```[\s\S]*?```", "", text)         # code blocks
    text = re.sub(r"\[(.+?)\]\(.+?\)", r"\1", text)   # links
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)  # headers
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)  # bullets
    text = re.sub(r"https?://\S+", "a link", text)    # URLs
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ─────────────────────────────────────────────
# Quick test
# ─────────────────────────────────────────────

async def _demo():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")
    tts = TTS({"tts_provider": "pyttsx3"})
    print(f"\nProviders: {tts.list_providers()}")
    print("Speaking test phrase...")
    result = await tts.speak("Good evening. I am JARVIS, your AI assistant. All systems operational.")
    print(f"Done. Provider: {result.provider} | {result.latency_ms:.0f}ms")


if __name__ == "__main__":
    asyncio.run(_demo())
