"""
JARVIS Speech-to-Text — voice/stt.py
Converts spoken audio to text.
Primary:  faster-whisper (local, offline, best quality)
Fallback: Google Speech API (free tier, needs internet)
Fallback: DeepGram (cloud, paid, best accuracy)
Fallback: AssemblyAI (cloud, paid)
Fallback: SpeechRecognition (free Google/Sphinx)
"""

import os
import io
import time
import logging
import asyncio
import platform
import tempfile
import threading
import queue
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

import numpy as np

logger = logging.getLogger("jarvis.stt")
OS = platform.system()


@dataclass
class STTResult:
    text: str
    language: str
    confidence: float
    provider: str
    latency_ms: float
    success: bool = True
    error: Optional[str] = None


# ─────────────────────────────────────────────
# Backend: faster-whisper (local)
# ─────────────────────────────────────────────

class WhisperSTT:
    """
    OpenAI Whisper running locally via faster-whisper.
    Models: tiny, base, small, medium, large-v3
    tiny  = fastest, ~39MB, good enough for commands
    small = great balance, ~244MB
    large = best accuracy, ~1.5GB
    """
    name = "whisper"

    def __init__(self, cfg: dict):
        self.model_size = cfg.get("model_size", "small")
        self.device = cfg.get("device", "auto")      # cpu, cuda, auto
        self.compute = cfg.get("compute_type", "int8")
        self.language = cfg.get("language", None)    # None = auto-detect
        self._model = None
        self._available = False
        self._lock = threading.Lock()
        self._init()

    def _init(self):
        try:
            from faster_whisper import WhisperModel
            # Auto-select device
            device = self.device
            if device == "auto":
                try:
                    import torch
                    device = "cuda" if torch.cuda.is_available() else "cpu"
                except ImportError:
                    device = "cpu"

            compute = self.compute
            if device == "cpu" and compute == "float16":
                compute = "int8"  # float16 not supported on CPU

            logger.info(f"Loading Whisper '{self.model_size}' on {device}...")
            self._model = WhisperModel(self.model_size, device=device, compute_type=compute)
            self._available = True
            logger.info(f"✅ Whisper STT ready ({self.model_size}, {device})")
        except ImportError:
            logger.warning("faster-whisper not installed: pip install faster-whisper")
        except Exception as e:
            logger.warning(f"Whisper init failed: {e}")

    @property
    def available(self) -> bool:
        return self._available

    async def transcribe(self, audio_data: np.ndarray, sample_rate: int = 16000) -> STTResult:
        if not self._model:
            return STTResult("", "en", 0.0, self.name, 0, False, "Model not loaded")

        t0 = time.time()
        try:
            # Save to temp WAV file (faster-whisper needs file or numpy)
            audio_float = audio_data.astype(np.float32) / 32768.0

            with self._lock:
                segments, info = await asyncio.to_thread(
                    self._model.transcribe,
                    audio_float,
                    language=self.language,
                    beam_size=5,
                    vad_filter=True,         # Remove silence
                    vad_parameters=dict(min_silence_duration_ms=300)
                )
                text = " ".join(s.text.strip() for s in segments).strip()

            return STTResult(
                text=text,
                language=info.language,
                confidence=info.language_probability,
                provider=self.name,
                latency_ms=(time.time() - t0) * 1000
            )
        except Exception as e:
            logger.error(f"Whisper transcription error: {e}")
            return STTResult("", "en", 0.0, self.name, 0, False, str(e))


# ─────────────────────────────────────────────
# Backend: DeepGram (cloud)
# ─────────────────────────────────────────────

class DeepGramSTT:
    """DeepGram — best cloud accuracy, very fast."""
    name = "deepgram"

    def __init__(self, cfg: dict):
        self.api_key = cfg.get("api_key") or os.getenv("DEEPGRAM_API_KEY", "")
        self.model = cfg.get("model", "nova-2")
        self._available = bool(self.api_key)

    @property
    def available(self) -> bool:
        return self._available

    async def transcribe(self, audio_data: np.ndarray, sample_rate: int = 16000) -> STTResult:
        t0 = time.time()
        try:
            from deepgram import DeepgramClient, PrerecordedOptions
            client = DeepgramClient(self.api_key)
            raw = audio_data.astype(np.int16).tobytes()
            options = PrerecordedOptions(model=self.model, language="en", smart_format=True)
            payload = {"buffer": raw, "mimetype": f"audio/raw;encoding=linear-pcm;sample_rate={sample_rate};bit_depth=16"}
            response = await asyncio.to_thread(client.listen.prerecorded.v("1").transcribe_file, payload, options)
            text = response.results.channels[0].alternatives[0].transcript
            conf = response.results.channels[0].alternatives[0].confidence
            return STTResult(text, "en", conf, self.name, (time.time() - t0) * 1000)
        except Exception as e:
            return STTResult("", "en", 0.0, self.name, 0, False, str(e))


# ─────────────────────────────────────────────
# Backend: AssemblyAI (cloud)
# ─────────────────────────────────────────────

class AssemblyAISTT:
    """AssemblyAI — excellent accuracy, supports many languages."""
    name = "assemblyai"

    def __init__(self, cfg: dict):
        self.api_key = cfg.get("api_key") or os.getenv("ASSEMBLYAI_API_KEY", "")
        self._available = bool(self.api_key)

    @property
    def available(self) -> bool:
        return self._available

    async def transcribe(self, audio_data: np.ndarray, sample_rate: int = 16000) -> STTResult:
        t0 = time.time()
        try:
            import assemblyai as aai
            aai.settings.api_key = self.api_key
            raw = audio_data.astype(np.int16).tobytes()
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                _write_wav(f.name, raw, sample_rate)
                tmp = f.name
            transcriber = aai.Transcriber()
            transcript = await asyncio.to_thread(transcriber.transcribe, tmp)
            os.unlink(tmp)
            return STTResult(transcript.text or "", "en", 0.9, self.name, (time.time() - t0) * 1000)
        except Exception as e:
            return STTResult("", "en", 0.0, self.name, 0, False, str(e))


# ─────────────────────────────────────────────
# Backend: SpeechRecognition (free fallback)
# ─────────────────────────────────────────────

class SpeechRecognitionSTT:
    """Uses the `speech_recognition` library — free Google API (no key needed)."""
    name = "google_free"

    def __init__(self):
        self._available = False
        try:
            import speech_recognition
            self._sr = speech_recognition
            self._available = True
            logger.info("SpeechRecognition fallback ready")
        except ImportError:
            logger.debug("speech_recognition not installed")

    @property
    def available(self) -> bool:
        return self._available

    async def transcribe(self, audio_data: np.ndarray, sample_rate: int = 16000) -> STTResult:
        t0 = time.time()
        try:
            sr = self._sr
            recognizer = sr.Recognizer()
            raw = audio_data.astype(np.int16).tobytes()
            audio = sr.AudioData(raw, sample_rate, 2)
            text = await asyncio.to_thread(recognizer.recognize_google, audio)
            return STTResult(text, "en", 0.8, self.name, (time.time() - t0) * 1000)
        except Exception as e:
            return STTResult("", "en", 0.0, self.name, 0, False, str(e))


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _write_wav(path: str, raw: bytes, sample_rate: int):
    import wave
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(raw)


# ─────────────────────────────────────────────
# Audio Recorder
# ─────────────────────────────────────────────

class AudioRecorder:
    """
    Records audio from microphone until silence is detected.
    VAD (Voice Activity Detection) to auto-stop recording.
    """

    SAMPLE_RATE = 16000
    CHANNELS = 1
    DTYPE = "int16"
    SILENCE_THRESHOLD = 500        # RMS below this = silence
    SILENCE_DURATION = 1.5         # seconds of silence to stop
    MAX_DURATION = 30              # max recording length in seconds
    PRE_ROLL_FRAMES = 5            # frames to keep before speech starts

    def __init__(self):
        self._frames: list = []

    async def record_until_silence(self) -> np.ndarray:
        """Record until the user stops speaking."""
        import sounddevice as sd

        logger.info("🎙 Listening...")
        frames = []
        silent_frames = 0
        silence_limit = int(self.SILENCE_DURATION * self.SAMPLE_RATE / 1024)
        max_frames = int(self.MAX_DURATION * self.SAMPLE_RATE / 1024)
        speech_started = False

        audio_q: queue.Queue = queue.Queue()

        def callback(indata, frames_count, time_info, status):
            audio_q.put(indata.copy())

        with sd.InputStream(
            samplerate=self.SAMPLE_RATE,
            channels=self.CHANNELS,
            dtype=self.DTYPE,
            blocksize=1024,
            callback=callback
        ):
            frame_count = 0
            while frame_count < max_frames:
                try:
                    chunk = audio_q.get(timeout=0.1)
                    pcm = chunk.flatten()
                    rms = np.sqrt(np.mean(pcm.astype(np.float32) ** 2))

                    if rms > self.SILENCE_THRESHOLD:
                        speech_started = True
                        silent_frames = 0
                        frames.append(pcm)
                    elif speech_started:
                        frames.append(pcm)
                        silent_frames += 1
                        if silent_frames >= silence_limit:
                            logger.info("🔇 Silence detected, done recording.")
                            break
                    # Pre-roll: keep a small buffer before speech
                    elif not speech_started:
                        frames.append(pcm)
                        if len(frames) > self.PRE_ROLL_FRAMES:
                            frames.pop(0)

                    frame_count += 1
                except queue.Empty:
                    if speech_started:
                        break
                    await asyncio.sleep(0)

        if not frames:
            return np.array([], dtype=np.int16)
        return np.concatenate(frames).astype(np.int16)

    async def record_fixed(self, duration: float = 5.0) -> np.ndarray:
        """Record for a fixed number of seconds."""
        import sounddevice as sd
        logger.info(f"🎙 Recording for {duration}s...")
        audio = await asyncio.to_thread(
            sd.rec,
            int(duration * self.SAMPLE_RATE),
            samplerate=self.SAMPLE_RATE,
            channels=self.CHANNELS,
            dtype=self.DTYPE
        )
        await asyncio.to_thread(sd.wait)
        return audio.flatten()


# ─────────────────────────────────────────────
# STT Manager (with fallback)
# ─────────────────────────────────────────────

class STT:
    """
    JARVIS Speech-to-Text manager.
    Tries providers in order: whisper → deepgram → assemblyai → google_free
    """

    def __init__(self, config: dict = None):
        cfg = config or {}
        self.recorder = AudioRecorder()
        priority_str = cfg.get("stt_provider", "whisper")

        # Build provider list
        all_providers = {
            "whisper":     WhisperSTT(cfg.get("whisper", {"model_size": "small"})),
            "deepgram":    DeepGramSTT(cfg.get("deepgram", {})),
            "assemblyai":  AssemblyAISTT(cfg.get("assemblyai", {})),
            "google_free": SpeechRecognitionSTT(),
        }

        # Sort: preferred first, then rest
        order = [priority_str] + [k for k in all_providers if k != priority_str]
        self.providers = {k: all_providers[k] for k in order if all_providers[k].available}

        if not self.providers:
            logger.error("No STT providers available!")
        else:
            logger.info(f"STT providers: {list(self.providers.keys())}")

    async def listen(self) -> STTResult:
        """Record from mic until silence, then transcribe."""
        audio = await self.recorder.record_until_silence()
        if len(audio) < 1000:
            return STTResult("", "en", 0.0, "none", 0, False, "No audio captured")
        return await self.transcribe(audio)

    async def transcribe(self, audio: np.ndarray, sample_rate: int = 16000) -> STTResult:
        """Transcribe pre-recorded audio."""
        for name, provider in self.providers.items():
            result = await provider.transcribe(audio, sample_rate)
            if result.success and result.text.strip():
                logger.info(f"[{name}] '{result.text}' ({result.latency_ms:.0f}ms)")
                return result
            else:
                logger.warning(f"[{name}] failed: {result.error}")

        return STTResult("", "en", 0.0, "none", 0, False, "All STT providers failed")

    async def transcribe_file(self, path: str) -> STTResult:
        """Transcribe an audio file."""
        try:
            import soundfile as sf
            audio, sr = sf.read(path, dtype="int16")
            return await self.transcribe(audio, sr)
        except Exception as e:
            return STTResult("", "en", 0.0, "none", 0, False, str(e))


# ─────────────────────────────────────────────
# Quick test
# ─────────────────────────────────────────────

async def _demo():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")
    stt = STT({"stt_provider": "whisper", "whisper": {"model_size": "tiny"}})
    print("\nSpeak now (recording for 4 seconds)...")
    audio = await stt.recorder.record_fixed(4.0)
    result = await stt.transcribe(audio)
    print(f"\n📝 Transcription: '{result.text}'")
    print(f"   Provider: {result.provider} | {result.latency_ms:.0f}ms | lang: {result.language}")


if __name__ == "__main__":
    asyncio.run(_demo())
