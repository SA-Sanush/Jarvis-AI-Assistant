"""
JARVIS HTTP Server — server.py
Lightweight aiohttp REST server that bridges the Electron UI
to the JARVIS Python backend (Brain + Memory + Voice + PC Control).

Endpoints:
  POST /chat              — send a message, get a response
  POST /voice/start       — record one utterance and transcribe
  POST /voice/stop        — cancel active recording
  GET  /status            — provider + memory status
  GET  /history           — recent conversation
  POST /history/clear     — wipe conversation history
  GET  /settings          — read settings
  POST /settings/save     — persist settings
  GET  /system/info       — CPU, RAM, disk, battery
"""

import os
import sys
import json
import asyncio
import logging
import argparse
import platform
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from aiohttp import web

# Add project root to path
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from core.jarvis import JARVIS
from skills.os_layer import get_system_info, get_battery

logger = logging.getLogger("jarvis.server")

# ─── Global instances ───────────────────────────────────────
jarvis: JARVIS = None
stt_instance = None
_recording = False


# ─── Route handlers ─────────────────────────────────────────

async def handle_chat(request: web.Request) -> web.Response:
    try:
        body = await request.json()
        message = body.get("message", "").strip()
        if not message:
            return _json({"error": "Empty message"}, status=400)

        response = await jarvis.ask(message)

        # Get provider info from last brain response
        brain = jarvis.brain
        last_provider = getattr(brain, "_last_provider", "unknown")
        last_model = getattr(brain, "_last_model", "")

        return _json({
            "text": response,
            "provider": last_provider,
            "model": last_model,
            "success": True
        })
    except Exception as e:
        logger.error(f"Chat error: {e}")
        return _json({"error": str(e), "success": False}, status=500)


async def handle_voice_start(request: web.Request) -> web.Response:
    global _recording
    try:
        from voice.stt import STT
        global stt_instance
        if stt_instance is None:
            cfg = jarvis.cfg.get("voice", {})
            stt_instance = STT(cfg)

        _recording = True
        result = await stt_instance.listen()
        _recording = False

        if result.success and result.text.strip():
            return _json({"text": result.text, "success": True,
                          "provider": result.provider, "latency_ms": result.latency_ms})
        else:
            return _json({"text": "", "success": False,
                          "error": result.error or "No speech detected"})
    except Exception as e:
        _recording = False
        logger.error(f"Voice start error: {e}")
        return _json({"error": str(e), "success": False}, status=500)


async def handle_voice_stop(request: web.Request) -> web.Response:
    global _recording
    _recording = False
    return _json({"success": True})


async def handle_status(request: web.Request) -> web.Response:
    try:
        status = await jarvis.status()
        return _json(status)
    except Exception as e:
        return _json({"error": str(e)}, status=500)


async def handle_history(request: web.Request) -> web.Response:
    try:
        messages = jarvis.memory.get_context(n=50)
        return _json({"messages": messages, "success": True})
    except Exception as e:
        return _json({"error": str(e)}, status=500)


async def handle_history_clear(request: web.Request) -> web.Response:
    try:
        jarvis.memory.new_session()
        return _json({"success": True})
    except Exception as e:
        return _json({"error": str(e)}, status=500)


async def handle_settings_get(request: web.Request) -> web.Response:
    try:
        settings_path = ROOT / "config" / "ui_settings.json"
        if settings_path.exists():
            settings = json.loads(settings_path.read_text())
        else:
            settings = _default_settings()
        return _json(settings)
    except Exception as e:
        return _json({"error": str(e)}, status=500)


async def handle_settings_save(request: web.Request) -> web.Response:
    try:
        body = await request.json()
        settings_path = ROOT / "config" / "ui_settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(json.dumps(body, indent=2))
        return _json({"success": True})
    except Exception as e:
        return _json({"error": str(e)}, status=500)


async def handle_system_info(request: web.Request) -> web.Response:
    try:
        info = get_system_info()
        battery = get_battery()
        if battery:
            info["battery"] = battery
        return _json(info)
    except Exception as e:
        return _json({"error": str(e)}, status=500)


async def handle_health(request: web.Request) -> web.Response:
    return _json({"status": "ok", "version": JARVIS.VERSION})


async def handle_reminder_alert(request: web.Request) -> web.Response:
    """Called by SkillRouter when a reminder fires — pushed to UI."""
    body = await request.json()
    title = body.get("title", "Reminder")
    # Broadcast to all connected clients via SSE or WebSocket in future
    # For now, log and return OK
    logger.info(f"🔔 Reminder alert: {title}")
    return _json({"success": True})


async def handle_skills_status(request: web.Request) -> web.Response:
    try:
        if jarvis and jarvis.skills:
            return _json(jarvis.skills.status())
        return _json({"skills": [], "plugins": None})
    except Exception as e:
        return _json({"error": str(e)}, status=500)


async def handle_plugin_list(request: web.Request) -> web.Response:
    try:
        if jarvis and jarvis.skills and jarvis.skills.plugins:
            return _json({"plugins": jarvis.skills.plugins.list_plugins()})
        return _json({"plugins": []})
    except Exception as e:
        return _json({"error": str(e)}, status=500)


async def handle_create_plugin(request: web.Request) -> web.Response:
    try:
        body = await request.json()
        name = body.get("name", "my_plugin")
        desc = body.get("description", "")
        triggers = body.get("triggers", [])
        if jarvis and jarvis.skills and jarvis.skills.plugins:
            path = jarvis.skills.plugins.create_plugin_template(name, desc, triggers)
            return _json({"success": True, "path": path})
        return _json({"success": False, "error": "Plugin system not available"})
    except Exception as e:
        return _json({"error": str(e)}, status=500)


# ── Language endpoints ───────────────────────────────────────

async def handle_get_languages(request: web.Request) -> web.Response:
    """Return supported languages and current language."""
    try:
        if jarvis and jarvis.lang:
            return _json({
                "current": jarvis.lang.current_lang,
                "supported": jarvis.lang.list_languages(),
                "translators": [t.name for t in jarvis.lang._translators if t.enabled],
            })
        return _json({"current": "en", "supported": [], "translators": []})
    except Exception as e:
        return _json({"error": str(e)}, status=500)


# ─── CORS middleware ────────────────────────────────────────

@web.middleware
async def cors_middleware(request, handler):
    if request.method == "OPTIONS":
        return web.Response(headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        })
    response = await handler(request)
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response


# ─── Helpers ────────────────────────────────────────────────

def _json(data: dict, status: int = 200) -> web.Response:
    return web.Response(
        text=json.dumps(data),
        content_type="application/json",
        status=status
    )

def _default_settings() -> dict:
    return {
        "wake_word_enabled": True,
        "streaming_tts": True,
        "web_search_enabled": True,
        "memory_enabled": True,
        "tts_provider": "pyttsx3",
        "whisper_model": "small",
        "ai_provider": "ollama",
    }


# ─── App factory ────────────────────────────────────────────



async def handle_chat_multilingual(request: web.Request) -> web.Response:
    """Chat endpoint with explicit language support."""
    try:
        body = await request.json()
        message = body.get("message", "").strip()
        input_lang = body.get("lang", None)       # e.g. "ml", "hi", "ta"
        response_lang = body.get("resp_lang", None)
        if not message:
            return _json({"error": "Empty message"}, status=400)

        response = await jarvis.chat(
            message,
            input_lang=input_lang,
            response_lang=response_lang
        )
        detected = jarvis.lang.detect(message)
        return _json({
            "text": response,
            "detected_lang": detected,
            "provider": getattr(jarvis.brain, "_last_provider", "unknown"),
            "success": True
        })
    except Exception as e:
        return _json({"error": str(e), "success": False}, status=500)


async def handle_detect_language(request: web.Request) -> web.Response:
    """Detect language of text."""
    try:
        body = await request.json()
        text = body.get("text", "")
        result = jarvis.detect_language(text)
        return _json(result)
    except Exception as e:
        return _json({"error": str(e)}, status=500)


async def handle_set_language(request: web.Request) -> web.Response:
    """Set active language."""
    try:
        body = await request.json()
        lang = body.get("lang", "en")
        msg = jarvis.set_language(lang)
        return _json({"success": True, "message": msg, "lang": lang})
    except Exception as e:
        return _json({"error": str(e)}, status=500)


async def handle_translate(request: web.Request) -> web.Response:
    """Translate text between languages."""
    try:
        body = await request.json()
        text = body.get("text", "")
        target = body.get("target", "en")
        source = body.get("source", "auto")
        result = await jarvis.lang.translate(text, target, source)
        return _json({
            "translated": result.text,
            "source_lang": result.source_lang,
            "target_lang": result.target_lang,
            "provider": result.provider,
            "success": result.success
        })
    except Exception as e:
        return _json({"error": str(e)}, status=500)


async def handle_language_status(request: web.Request) -> web.Response:
    """Get language system status."""
    try:
        return _json({
            "current": jarvis.lang.current_lang,
            "supported": jarvis.lang.list_languages(),
            "translators": [t.name for t in jarvis.lang._translators if t.enabled],
            "auto_detect": jarvis.lang.auto_detect,
        })
    except Exception as e:
        return _json({"error": str(e)}, status=500)

def create_app() -> web.Application:
    app = web.Application(middlewares=[cors_middleware])
    app.router.add_post("/chat", handle_chat)
    app.router.add_post("/voice/start", handle_voice_start)
    app.router.add_post("/voice/stop", handle_voice_stop)
    app.router.add_get("/status", handle_status)
    app.router.add_get("/history", handle_history)
    app.router.add_post("/history/clear", handle_history_clear)
    app.router.add_get("/settings", handle_settings_get)
    app.router.add_post("/settings/save", handle_settings_save)
    app.router.add_get("/system/info", handle_system_info)
    app.router.add_get("/health", handle_health)
    app.router.add_post("/chat/ml", handle_chat_multilingual)
    app.router.add_post("/lang/detect", handle_detect_language)
    app.router.add_post("/lang/set", handle_set_language)
    app.router.add_post("/lang/translate", handle_translate)
    app.router.add_get("/lang/status", handle_language_status)
    app.router.add_post("/reminder_alert", handle_reminder_alert)
    app.router.add_get("/skills/status", handle_skills_status)
    app.router.add_get("/skills/plugins", handle_plugin_list)
    app.router.add_post("/skills/plugins/create", handle_create_plugin)
    # Language routes
    app.router.add_get("/language", handle_get_languages)
    app.router.add_post("/language/set", handle_set_language)
    app.router.add_post("/language/detect", handle_detect_language)
    app.router.add_post("/language/translate", handle_translate)
    return app


# ─── Entry point ────────────────────────────────────────────

async def main(port: int = 7771):
    global jarvis

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s | %(name)s | %(message)s"
    )

    logger.info("Initializing JARVIS core...")
    jarvis = JARVIS()
    loop = asyncio.get_event_loop()
    jarvis.start_skills(loop)   # Start alarm daemon + plugin watcher
    status = await jarvis.status()
    alive = [k for k, v in status.get("brain", {}).items() if v]
    skills = status.get("skills", {}).get("skills", [])
    logger.info(f"AI providers online: {alive}")
    logger.info(f"Skills active: {skills}")

    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()

    # Signal to Electron that we're ready
    print(f"JARVIS server ready on port {port}", flush=True)
    logger.info(f"Server listening on http://127.0.0.1:{port}")

    # Keep running
    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down JARVIS server...")
        await runner.cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=7771)
    args = parser.parse_args()
    asyncio.run(main(port=args.port))
