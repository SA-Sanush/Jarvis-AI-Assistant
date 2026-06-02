"""
JARVIS Voice Pipeline — voice/pipeline.py
The complete voice loop:
  [wake word] → [record audio] → [STT] → [Brain] → [TTS]

Supports:
- Continuous listening mode (always-on)
- Push-to-talk mode (keyboard/button trigger)
- One-shot recording (single utterance)
- Streaming TTS (starts speaking before full response)
"""

import asyncio
import logging
import platform
import time
from enum import Enum
from typing import Callable, Optional, AsyncIterator

from .wake_word import WakeWordDetector
from .stt import STT
from .tts import TTS

logger = logging.getLogger("jarvis.voice")
OS = platform.system()


class PipelineMode(str, Enum):
    WAKE_WORD   = "wake_word"    # Always listening, activates on "Jarvis"
    PUSH_TO_TALK = "push_to_talk"  # Hold a key/button to speak
    CONTINUOUS  = "continuous"   # Always recording (no wake word)


class VoicePipeline:
    """
    Full voice pipeline for JARVIS.
    wake word → STT → AI Brain → TTS
    """

    ACTIVATION_SOUND  = "Listening."
    THINKING_SOUND    = "Processing."
    ERROR_SOUND       = "I didn't catch that. Could you repeat?"
    BOOT_GREETING     = "Good day. JARVIS online. All systems nominal."

    def __init__(
        self,
        jarvis_instance,           # The core JARVIS orchestrator
        config: dict = None,
        mode: PipelineMode = PipelineMode.WAKE_WORD,
        on_listening: Callable = None,      # Called when recording starts
        on_processing: Callable = None,     # Called when STT is running
        on_response: Callable[[str], None] = None,  # Called with response text
    ):
        cfg = config or {}
        self.jarvis = jarvis_instance
        self.mode = mode
        self.cfg = cfg

        # Callbacks for UI integration
        self.on_listening = on_listening or (lambda: None)
        self.on_processing = on_processing or (lambda: None)
        self.on_response = on_response or (lambda t: None)

        # Sub-systems are initialized lazily to avoid blocking startup.
        self._stt_cfg = cfg.get("voice", {})
        self._tts_cfg = cfg.get("voice", {})
        self.stt: Optional[STT] = None
        self.tts: Optional[TTS] = None
        self.wake = WakeWordDetector(
            config=cfg.get("voice", {}),
            on_detected=self._handle_wake_word
        )

        self._running = False
        self._active = False          # True during a voice interaction
        self._wake_event = asyncio.Event()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        logger.info(f"Voice pipeline ready — mode: {self.mode}")

    # ── Entry points ───────────────────────────

    async def start(self):
        """Start the voice pipeline loop."""
        self._running = True
        self._loop = asyncio.get_event_loop()
        self._ensure_tts()

        await self.tts.speak(self.BOOT_GREETING)

        if self.mode == PipelineMode.WAKE_WORD:
            await self._run_wake_word_loop()
        elif self.mode == PipelineMode.PUSH_TO_TALK:
            await self._run_push_to_talk_loop()
        elif self.mode == PipelineMode.CONTINUOUS:
            await self._run_continuous_loop()

    def stop(self):
        self._running = False
        self.wake.stop()
        logger.info("Voice pipeline stopped.")

    # ── Wake word mode ─────────────────────────

    async def _run_wake_word_loop(self):
        """Continuously listen for wake word, then handle interaction."""
        logger.info(f"🎤 Wake word mode — say '{self.cfg.get('voice', {}).get('wake_word', 'jarvis')}'")
        self.wake.start(self._loop)

        while self._running:
            # Wait for wake word signal
            await self._wake_event.wait()
            self._wake_event.clear()

            if not self._running:
                break

            await self._handle_interaction()

        self.wake.stop()

    def _handle_wake_word(self):
        """Triggered from background thread when wake word detected."""
        if not self._active and self._loop:
            self._loop.call_soon_threadsafe(self._wake_event.set)

    # ── Push-to-talk mode ──────────────────────

    async def _run_push_to_talk_loop(self):
        """Press SPACE to talk, release to process."""
        try:
            from pynput import keyboard
        except ImportError:
            logger.error("pynput not installed: pip install pynput")
            return

        logger.info("🎤 Push-to-talk mode — hold SPACE to speak")
        ptt_event = asyncio.Event()

        def on_press(key):
            if key == keyboard.Key.space and not self._active:
                self._loop.call_soon_threadsafe(ptt_event.set)

        listener = keyboard.Listener(on_press=on_press)
        listener.start()

        while self._running:
            await ptt_event.wait()
            ptt_event.clear()
            await self._handle_interaction()

        listener.stop()

    # ── Continuous mode ────────────────────────

    async def _run_continuous_loop(self):
        """Always recording, no wake word needed."""
        logger.info("🎤 Continuous mode — always listening")
        while self._running:
            await self._handle_interaction()
            await asyncio.sleep(0.1)

    # ── Core interaction ───────────────────────

    async def _handle_interaction(self):
        """
        One full interaction cycle:
        record → STT → Brain → TTS
        """
        self._active = True
        try:
            self._ensure_stt()

            # 1. Signal UI
            self.on_listening()
            logger.info("🎙 Recording user input...")

            # 2. Record audio
            stt_result = await self.stt.listen()

            if not stt_result.success or not stt_result.text.strip():
                await self.tts.speak(self.ERROR_SOUND)
                return

            text = stt_result.text.strip()
            logger.info(f"📝 Heard: '{text}'")

            # 3. Signal processing
            self.on_processing()

            # 4. Send to brain and speak response
            await self._think_and_speak(text)

        except Exception as e:
            logger.error(f"Voice interaction error: {e}")
            await self.tts.speak("I encountered an error. Please try again.")
        finally:
            self._active = False

    async def _think_and_speak(self, user_text: str):
        """Get AI response and speak it — with streaming TTS if available."""
        try:
            # Stream response tokens and speak sentence by sentence
            token_stream = await self.jarvis.chat(user_text, stream=True)
            full_response = []
            buffer = ""
            sentence_ends = {".", "!", "?"}

            async for token in token_stream:
                full_response.append(token)
                buffer += token

                # Speak when buffer reaches a sentence boundary
                if (any(buffer.rstrip().endswith(e) for e in sentence_ends)
                        and len(buffer) > 25):
                    await self.tts.speak(buffer.strip())
                    buffer = ""

            # Speak any remaining
            if buffer.strip():
                await self.tts.speak(buffer.strip())

            response_text = "".join(full_response)
            self.on_response(response_text)

        except Exception as e:
            logger.error(f"Think-and-speak error: {e}")
            await self.tts.speak("I had trouble generating a response.")

    # ── One-shot API ───────────────────────────

    async def listen_once(self) -> str:
        """Record one utterance and return the transcribed text."""
        self._ensure_stt()
        result = await self.stt.listen()
        return result.text if result.success else ""

    async def say(self, text: str):
        """Speak text immediately."""
        self._ensure_tts()
        await self.tts.speak(text)

    async def ask_and_respond(self, prompt: str = None) -> str:
        """
        Optionally say a prompt, then listen and respond.
        Returns the user's transcribed text.
        """
        self._ensure_tts()
        if prompt:
            await self.tts.speak(prompt)
        text = await self.listen_once()
        if text:
            await self._think_and_speak(text)
        return text

    def _ensure_stt(self):
        if self.stt is None:
            self.stt = STT(self._stt_cfg)

    def _ensure_tts(self):
        if self.tts is None:
            self.tts = TTS(self._tts_cfg)

    # ── Status ─────────────────────────────────

    def status(self) -> dict:
        return {
            "mode": self.mode,
            "running": self._running,
            "active": self._active,
            "stt_providers": list(self.stt.providers.keys()) if self.stt else [],
            "tts_providers": self.tts.list_providers() if self.tts else [],
            "wake_backend": self.wake._backend.name,
        }
