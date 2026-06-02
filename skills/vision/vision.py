"""
JARVIS Vision — skills/vision/vision.py
Eyes for JARVIS: screen reading, OCR, camera, object detection,
image understanding via Claude Vision / GPT-4V / local models.
"""

import os
import io
import time
import base64
import asyncio
import logging
import platform
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("jarvis.vision")
OS = platform.system()


@dataclass
class VisionResult:
    description: str
    objects: list[str] = None
    text_found: str = ""
    confidence: float = 1.0
    provider: str = ""
    latency_ms: float = 0.0
    success: bool = True
    error: Optional[str] = None


# ─────────────────────────────────────────────
# Image capture
# ─────────────────────────────────────────────

class ScreenCapture:
    """Capture screenshots and screen regions."""

    @staticmethod
    async def full_screen() -> bytes:
        """Capture entire screen as PNG bytes."""
        try:
            import pyautogui
            from PIL import Image
            import io
            img = await asyncio.to_thread(pyautogui.screenshot)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
        except ImportError:
            raise RuntimeError("pyautogui/pillow not installed")

    @staticmethod
    async def region(x: int, y: int, w: int, h: int) -> bytes:
        """Capture a screen region."""
        try:
            import pyautogui
            import io
            img = await asyncio.to_thread(pyautogui.screenshot, region=(x, y, w, h))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
        except ImportError:
            raise RuntimeError("pyautogui not installed")

    @staticmethod
    async def active_window() -> bytes:
        """Capture only the active window."""
        if OS == "Windows":
            try:
                import pygetwindow as gw
                import pyautogui
                import io
                win = gw.getActiveWindow()
                if win:
                    img = await asyncio.to_thread(
                        pyautogui.screenshot,
                        region=(win.left, win.top, win.width, win.height)
                    )
                    buf = io.BytesIO()
                    img.save(buf, format="PNG")
                    return buf.getvalue()
            except ImportError:
                pass
        return await ScreenCapture.full_screen()

    @staticmethod
    async def from_file(path: str) -> bytes:
        return Path(path).read_bytes()

    @staticmethod
    async def from_url(url: str) -> bytes:
        import aiohttp
        async with aiohttp.ClientSession() as s:
            async with s.get(url) as r:
                return await r.read()


class CameraCapture:
    """Capture from webcam."""

    def __init__(self, camera_index: int = 0):
        self.index = camera_index

    async def capture(self) -> bytes:
        """Take a photo from webcam."""
        try:
            import cv2
            import io
            from PIL import Image

            def _snap():
                cap = cv2.VideoCapture(self.index)
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
                time.sleep(0.3)   # Let camera warm up
                ret, frame = cap.read()
                cap.release()
                if not ret:
                    raise RuntimeError("Could not capture from camera")
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                pil_img = Image.fromarray(rgb)
                buf = io.BytesIO()
                pil_img.save(buf, format="PNG")
                return buf.getvalue()

            return await asyncio.to_thread(_snap)
        except ImportError:
            raise RuntimeError("opencv-python not installed: pip install opencv-python pillow")

    async def start_stream(self, callback, fps: int = 2):
        """Stream frames at given FPS, calling callback(frame_bytes) each time."""
        try:
            import cv2
            import io
            from PIL import Image

            cap = cv2.VideoCapture(self.index)
            interval = 1.0 / fps

            while True:
                ret, frame = cap.read()
                if not ret:
                    break
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                from PIL import Image as PILImage
                pil = PILImage.fromarray(rgb)
                buf = io.BytesIO()
                pil.save(buf, format="JPEG", quality=70)
                await callback(buf.getvalue())
                await asyncio.sleep(interval)

            cap.release()
        except ImportError:
            raise RuntimeError("opencv-python not installed")


# ─────────────────────────────────────────────
# OCR engines
# ─────────────────────────────────────────────

class TesseractOCR:
    """Local OCR using Tesseract."""

    def __init__(self):
        self._available = False
        try:
            import pytesseract
            self._pytesseract = pytesseract
            self._available = True
        except ImportError:
            pass

    @property
    def available(self) -> bool:
        return self._available

    async def extract_text(self, image_bytes: bytes) -> str:
        if not self._available:
            return ""
        try:
            from PIL import Image
            import io
            img = Image.open(io.BytesIO(image_bytes))
            text = await asyncio.to_thread(self._pytesseract.image_to_string, img)
            return text.strip()
        except Exception as e:
            logger.warning(f"Tesseract OCR error: {e}")
            return ""


class EasyOCR:
    """EasyOCR — better accuracy than Tesseract, supports 80+ languages."""

    def __init__(self, languages: list = None):
        self.languages = languages or ["en"]
        self._reader = None
        self._available = False
        self._init()

    def _init(self):
        try:
            import easyocr
            self._reader = easyocr.Reader(self.languages, gpu=False, verbose=False)
            self._available = True
            logger.info("EasyOCR ready")
        except ImportError:
            pass

    @property
    def available(self) -> bool:
        return self._available

    async def extract_text(self, image_bytes: bytes) -> str:
        if not self._available:
            return ""
        try:
            import numpy as np
            from PIL import Image
            import io
            img = Image.open(io.BytesIO(image_bytes))
            img_array = np.array(img)
            results = await asyncio.to_thread(self._reader.readtext, img_array)
            return " ".join([text for _, text, conf in results if conf > 0.5])
        except Exception as e:
            logger.warning(f"EasyOCR error: {e}")
            return ""


# ─────────────────────────────────────────────
# AI vision providers
# ─────────────────────────────────────────────

class ClaudeVision:
    """Use Claude's vision to understand images."""

    def __init__(self):
        self.api_key = os.getenv("ANTHROPIC_API_KEY", "")
        self.available = bool(self.api_key)

    async def describe(self, image_bytes: bytes, prompt: str = "Describe this image in detail.") -> VisionResult:
        t0 = time.time()
        try:
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=self.api_key)
            b64 = base64.b64encode(image_bytes).decode()
            response = await client.messages.create(
                model="claude-opus-4-5",
                max_tokens=1024,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                        {"type": "text", "text": prompt}
                    ]
                }]
            )
            return VisionResult(
                description=response.content[0].text,
                provider="claude",
                latency_ms=(time.time() - t0) * 1000
            )
        except Exception as e:
            return VisionResult("", provider="claude", latency_ms=0, success=False, error=str(e))


class OpenAIVision:
    """Use GPT-4o vision."""

    def __init__(self):
        self.api_key = os.getenv("OPENAI_API_KEY", "")
        self.available = bool(self.api_key)

    async def describe(self, image_bytes: bytes, prompt: str = "Describe this image in detail.") -> VisionResult:
        t0 = time.time()
        try:
            import openai
            client = openai.AsyncOpenAI(api_key=self.api_key)
            b64 = base64.b64encode(image_bytes).decode()
            response = await client.chat.completions.create(
                model="gpt-4o",
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                        {"type": "text", "text": prompt}
                    ]
                }],
                max_tokens=1024
            )
            return VisionResult(
                description=response.choices[0].message.content,
                provider="gpt4o",
                latency_ms=(time.time() - t0) * 1000
            )
        except Exception as e:
            return VisionResult("", provider="gpt4o", latency_ms=0, success=False, error=str(e))


class LocalObjectDetection:
    """YOLO-based local object detection (no API key needed)."""

    def __init__(self):
        self._model = None
        self._available = False
        self._init()

    def _init(self):
        try:
            from ultralytics import YOLO
            self._model = YOLO("yolov8n.pt")   # Nano model, downloads ~6MB
            self._available = True
            logger.info("YOLOv8 object detection ready")
        except ImportError:
            pass

    @property
    def available(self) -> bool:
        return self._available

    async def detect(self, image_bytes: bytes) -> list[str]:
        if not self._available:
            return []
        try:
            import numpy as np
            from PIL import Image
            import io
            img = Image.open(io.BytesIO(image_bytes))
            results = await asyncio.to_thread(self._model.predict, img, verbose=False)
            objects = []
            for r in results:
                for box in r.boxes:
                    name = r.names[int(box.cls)]
                    conf = float(box.conf)
                    if conf > 0.5:
                        objects.append(f"{name} ({conf:.0%})")
            return list(set(objects))
        except Exception as e:
            logger.warning(f"YOLO detection error: {e}")
            return []


# ─────────────────────────────────────────────
# Vision Manager
# ─────────────────────────────────────────────

class Vision:
    """
    JARVIS Vision — unified interface.
    Combines screen capture + OCR + AI understanding + object detection.
    """

    def __init__(self, config: dict = None):
        cfg = config or {}
        self.screen = ScreenCapture()
        self.camera = CameraCapture(cfg.get("camera_index", 0))

        # OCR (try EasyOCR first, Tesseract fallback)
        self._ocr = EasyOCR() if EasyOCR().available else TesseractOCR()

        # AI vision (try Claude first, GPT-4o fallback)
        self._claude_vision = ClaudeVision()
        self._openai_vision = OpenAIVision()

        # Local detection
        self._detector = LocalObjectDetection()

        logger.info(f"Vision ready | OCR: {type(self._ocr).__name__} | "
                    f"AI: {'Claude' if self._claude_vision.available else 'GPT-4o' if self._openai_vision.available else 'None'}")

    # ── Main API ────────────────────────────────────────────

    async def look_at_screen(self, prompt: str = None) -> VisionResult:
        """Capture screen and describe it using AI."""
        image = await self.screen.full_screen()
        return await self._describe(image, prompt or "What is on this screen? Describe what you see in detail.")

    async def look_at_camera(self, prompt: str = None) -> VisionResult:
        """Take a photo and describe it."""
        image = await self.camera.capture()
        return await self._describe(image, prompt or "What do you see in this photo?")

    async def read_screen(self) -> str:
        """OCR the current screen — extract all text."""
        image = await self.screen.full_screen()
        return await self._ocr.extract_text(image)

    async def read_image(self, path: str) -> str:
        """OCR a local image file."""
        image = await ScreenCapture.from_file(path)
        return await self._ocr.extract_text(image)

    async def describe_image(self, path_or_url: str, prompt: str = None) -> VisionResult:
        """Describe an image file or URL."""
        if path_or_url.startswith("http"):
            image = await ScreenCapture.from_url(path_or_url)
        else:
            image = await ScreenCapture.from_file(path_or_url)
        return await self._describe(image, prompt or "Describe this image in detail.")

    async def detect_objects_on_screen(self) -> list[str]:
        """Detect objects on current screen using YOLO."""
        image = await self.screen.full_screen()
        return await self._detector.detect(image)

    async def find_text_on_screen(self, target_text: str) -> bool:
        """Check if specific text appears on screen."""
        screen_text = await self.read_screen()
        return target_text.lower() in screen_text.lower()

    async def screenshot_and_describe(self, save_path: str = None) -> VisionResult:
        """Screenshot the screen, save it, and describe it."""
        image = await self.screen.full_screen()
        if save_path:
            Path(save_path).write_bytes(image)
        result = await self._describe(image)
        result.description = f"Screenshot: {save_path or '(not saved)'}\n\n{result.description}"
        return result

    # ── Natural language handler ────────────────────────────

    async def handle(self, command: str) -> Optional[str]:
        """Route natural language vision commands."""
        cmd = command.lower()

        if any(k in cmd for k in ["what's on my screen", "read my screen", "what do you see on screen", "look at screen"]):
            result = await self.look_at_screen()
            return result.description if result.success else f"Vision error: {result.error}"

        if any(k in cmd for k in ["read screen", "ocr", "extract text from screen", "what text is on"]):
            text = await self.read_screen()
            return f"Text on screen:\n{text}" if text else "No text found on screen."

        if any(k in cmd for k in ["take a photo", "camera", "what do you see", "look through camera"]):
            result = await self.look_at_camera()
            return result.description if result.success else f"Camera error: {result.error}"

        if any(k in cmd for k in ["what objects", "detect objects", "what's in the room"]):
            objects = await self.detect_objects_on_screen()
            return f"Objects detected: {', '.join(objects)}" if objects else "No objects detected."

        return None

    # ── Internal ────────────────────────────────────────────

    async def _describe(self, image_bytes: bytes, prompt: str = "Describe this image.") -> VisionResult:
        """Use best available AI vision provider."""
        if self._claude_vision.available:
            result = await self._claude_vision.describe(image_bytes, prompt)
            if result.success:
                return result

        if self._openai_vision.available:
            result = await self._openai_vision.describe(image_bytes, prompt)
            if result.success:
                return result

        # Fallback: OCR only
        text = await self._ocr.extract_text(image_bytes)
        return VisionResult(
            description=f"(OCR only — no AI vision available)\nText found: {text or 'None'}",
            text_found=text,
            provider="ocr"
        )
