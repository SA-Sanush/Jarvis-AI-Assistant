"""
JARVIS Wake Word Detector — voice/wake_word.py
Listens continuously for the wake word "Jarvis".
Primary:  Porcupine (Picovoice) — most accurate, runs on CPU
Fallback: Vosk (offline) — open source, no account needed
Fallback: Simple energy + keyword match — zero dependencies
"""

import os
import time
import queue
import logging
import asyncio
import platform
import threading
from typing import Callable, Optional

import numpy as np

logger = logging.getLogger("jarvis.wake_word")
OS = platform.system()


# ─────────────────────────────────────────────
# Backend: Porcupine (Picovoice)
# ─────────────────────────────────────────────

class PorcupineWakeWord:
    """
    Best accuracy. Free tier: 1 wake word, unlimited devices.
    Get key: https://console.picovoice.ai/
    """
    name = "porcupine"

    def __init__(self, access_key: str, keyword: str = "jarvis", sensitivity: float = 0.6):
        self.access_key = access_key
        self.keyword = keyword.lower()
        self.sensitivity = sensitivity
        self._porcupine = None
        self._available = False
        self._init()

    def _init(self):
        try:
            import pvporcupine
            # Built-in keywords: alexa, computer, hey google, hey siri, jarvis, ok google, picovoice, porcupine, terminator, americano, blueberry, bumblebee, grapefruit, grasshopper, hey barista, hey mycroft, pineapple, smart mirror, snowboy
            self._porcupine = pvporcupine.create(
                access_key=self.access_key,
                keywords=[self.keyword],
                sensitivities=[self.sensitivity]
            )
            self._available = True
            logger.info(f"Porcupine ready — listening for '{self.keyword}'")
        except ImportError:
            logger.debug("pvporcupine not installed")
        except Exception as e:
            logger.warning(f"Porcupine init failed: {e}")

    @property
    def available(self) -> bool:
        return self._available

    @property
    def frame_length(self) -> int:
        return self._porcupine.frame_length if self._porcupine else 512

    @property
    def sample_rate(self) -> int:
        return self._porcupine.sample_rate if self._porcupine else 16000

    def process(self, pcm: np.ndarray) -> bool:
        """Returns True if wake word detected."""
        if not self._porcupine:
            return False
        idx = self._porcupine.process(pcm.astype(np.int16))
        return idx >= 0

    def delete(self):
        if self._porcupine:
            self._porcupine.delete()


# ─────────────────────────────────────────────
# Backend: Vosk (offline, open source)
# ─────────────────────────────────────────────

class VoskWakeWord:
    """
    Open-source offline recognition.
    Download small model: https://alphacephei.com/vosk/models (vosk-model-small-en-us)
    """
    name = "vosk"

    def __init__(self, model_path: str = None, keyword: str = "jarvis"):
        self.keyword = keyword.lower()
        self.model_path = model_path or os.path.expanduser("~/.jarvis/vosk-model-small-en-us")
        self._rec = None
        self._available = False
        self.sample_rate = 16000
        self.frame_length = 4000
        self._init()

    def _init(self):
        try:
            from vosk import Model, KaldiRecognizer
            import json as _json
            if not os.path.exists(self.model_path):
                logger.warning(f"Vosk model not found at {self.model_path}. "
                               "Download from https://alphacephei.com/vosk/models")
                return
            model = Model(self.model_path)
            self._rec = KaldiRecognizer(model, self.sample_rate)
            self._json = _json
            self._available = True
            logger.info(f"Vosk ready — listening for '{self.keyword}'")
        except ImportError:
            logger.debug("vosk not installed")
        except Exception as e:
            logger.warning(f"Vosk init failed: {e}")

    @property
    def available(self) -> bool:
        return self._available

    def process(self, pcm: np.ndarray) -> bool:
        if not self._rec:
            return False
        raw = pcm.astype(np.int16).tobytes()
        if self._rec.AcceptWaveform(raw):
            result = self._json.loads(self._rec.Result())
            text = result.get("text", "").lower()
            if self.keyword in text:
                logger.debug(f"Vosk recognized: '{text}'")
                return True
        return False

    def delete(self):
        self._rec = None


# ─────────────────────────────────────────────
# Backend: Simple energy + keyword (zero-dep fallback)
# ─────────────────────────────────────────────

class SimpleWakeWord:
    """
    Zero-dependency fallback.
    Uses energy detection + SpeechRecognition library (Google free tier or offline).
    """
    name = "simple"

    def __init__(self, keyword: str = "jarvis"):
        self.keyword = keyword.lower()
        self.sample_rate = 16000
        self.frame_length = 8000
        self._available = True  # Always available

    @property
    def available(self) -> bool:
        return True

    def process_audio_chunk(self, audio_data: bytes) -> bool:
        """Try speech recognition on audio chunk."""
        try:
            import speech_recognition as sr
            recognizer = sr.Recognizer()
            audio = sr.AudioData(audio_data, self.sample_rate, 2)
            text = recognizer.recognize_google(audio).lower()
            logger.debug(f"Heard: '{text}'")
            return self.keyword in text
        except Exception:
            return False

    def delete(self):
        pass


# ─────────────────────────────────────────────
# Wake Word Manager
# ─────────────────────────────────────────────

class WakeWordDetector:
    """
    JARVIS Wake Word Detector.
    Tries Porcupine → Vosk → Simple in order.
    Runs in a background thread, calls callback when wake word detected.
    """

    def __init__(self, config: dict = None, on_detected: Callable = None):
        cfg = config or {}
        self.keyword = cfg.get("wake_word", "jarvis")
        self.porcupine_key = cfg.get("porcupine_access_key") or os.getenv("PORCUPINE_ACCESS_KEY", "")
        self.vosk_model = cfg.get("vosk_model_path")
        self.on_detected = on_detected or (lambda: None)

        self._backend = self._pick_backend()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        logger.info(f"Wake word backend: {self._backend.name}")

    def _pick_backend(self):
        # 1. Porcupine
        if self.porcupine_key:
            p = PorcupineWakeWord(self.porcupine_key, self.keyword)
            if p.available:
                return p

        # 2. Vosk
        v = VoskWakeWord(self.vosk_model, self.keyword)
        if v.available:
            return v

        # 3. Simple
        logger.warning("Using simple wake word backend (less accurate). "
                       "Set PORCUPINE_ACCESS_KEY for best results.")
        return SimpleWakeWord(self.keyword)

    def start(self, loop: asyncio.AbstractEventLoop = None):
        """Start listening in background thread."""
        self._loop = loop or asyncio.get_event_loop()
        self._running = True
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()
        logger.info(f"🎤 Wake word detector started — say '{self.keyword}'")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        self._backend.delete()
        logger.info("Wake word detector stopped.")

    def _listen_loop(self):
        try:
            import sounddevice as sd
        except ImportError:
            logger.error("sounddevice not installed: pip install sounddevice")
            return

        sample_rate = getattr(self._backend, "sample_rate", 16000)
        frame_len = getattr(self._backend, "frame_length", 512)

        audio_q: queue.Queue = queue.Queue()

        def audio_callback(indata, frames, time_info, status):
            audio_q.put(indata.copy())

        with sd.InputStream(
            samplerate=sample_rate,
            channels=1,
            dtype="int16",
            blocksize=frame_len,
            callback=audio_callback
        ):
            logger.debug("Microphone stream open.")
            while self._running:
                try:
                    pcm = audio_q.get(timeout=0.5)
                    pcm = pcm.flatten()

                    detected = False
                    if isinstance(self._backend, (PorcupineWakeWord, VoskWakeWord)):
                        detected = self._backend.process(pcm)
                    elif isinstance(self._backend, SimpleWakeWord):
                        detected = self._backend.process_audio_chunk(pcm.tobytes())

                    if detected:
                        logger.info(f"🔔 Wake word detected: '{self.keyword}'")
                        if self._loop and self._loop.is_running():
                            asyncio.run_coroutine_threadsafe(
                                self._trigger(), self._loop
                            )
                        else:
                            self.on_detected()

                except queue.Empty:
                    continue
                except Exception as e:
                    logger.error(f"Wake word loop error: {e}")

    async def _trigger(self):
        if asyncio.iscoroutinefunction(self.on_detected):
            await self.on_detected()
        else:
            self.on_detected()
