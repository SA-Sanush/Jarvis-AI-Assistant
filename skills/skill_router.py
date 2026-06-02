"""
JARVIS Skill Router — skills/skill_router.py
Single entry point that chains all skill modules in priority order.
Order:
  1. Plugins         (user extensions — highest priority)
  2. Browser         (web automation)
  3. Vision          (screen / camera understanding)
  4. Productivity    (reminders, notes, todos, calendar, timers)
  5. PC Control      (app launch, files, system)
  6. → AI Brain      (everything else — handled by core/jarvis.py)
"""

import logging
import asyncio
from typing import Optional

logger = logging.getLogger("jarvis.skills")


class SkillRouter:
    """
    Chains all JARVIS skill modules.
    Each skill's handle() returns a string or None.
    First non-None result wins.
    """

    def __init__(self, jarvis_instance, config: dict = None):
        cfg = config or {}
        self._jarvis = jarvis_instance
        self._skills = []
        self.agent_enabled = cfg.get("system_access", {}).get("agent_control", True)
        self._load_skills(cfg)

    def _load_skills(self, cfg: dict):
        """Lazily load each skill module with graceful failure."""

        # 1. Plugin system (loads user plugins from ~/.jarvis/plugins/)
        try:
            from skills.plugins.plugin_manager import PluginManager, create_builtin_plugins
            self.plugins = PluginManager(self._jarvis)
            create_builtin_plugins()
            results = self.plugins.load_all()
            logger.info(f"Plugins: {len(results['loaded'])} loaded, {len(results['failed'])} failed")
            self._skills.append(("plugins", self.plugins.handle))
        except Exception as e:
            logger.warning(f"Plugin system unavailable: {e}")
            self.plugins = None

        # 2. Browser
        try:
            from skills.browser.browser import JarvisBrowser
            browser_cfg = cfg.get("browser", {})
            self.browser = JarvisBrowser(browser_cfg)
            self._skills.append(("browser", self.browser.handle))
            logger.info("Browser skill ready")
        except Exception as e:
            logger.warning(f"Browser skill unavailable: {e}")
            self.browser = None

        # 3. Vision
        try:
            from skills.vision.vision import Vision
            self.vision = Vision(cfg.get("vision", {}))
            self._skills.append(("vision", self.vision.handle))
            logger.info("Vision skill ready")
        except Exception as e:
            logger.warning(f"Vision skill unavailable: {e}")
            self.vision = None

        # 4. Productivity
        try:
            from skills.productivity.productivity import Productivity
            self.productivity = Productivity(
                on_reminder=self._reminder_alert
            )
            self._skills.append(("productivity", self.productivity.handle))
            logger.info("Productivity skill ready")
        except Exception as e:
            logger.warning(f"Productivity skill unavailable: {e}")
            self.productivity = None

        # 5. PC Control
        try:
            from skills.pc_control import PCControl
            self.pc = PCControl(cfg.get("system_access", {}))
            self._skills.append(("pc_control", self.pc.handle))
            logger.info("PC Control skill ready")
        except Exception as e:
            logger.warning(f"PC Control skill unavailable: {e}")
            self.pc = None

    def start(self, loop=None):
        """Start background daemons (alarm scheduler, plugin watcher)."""
        lp = loop or asyncio.get_event_loop()
        if self.productivity:
            self.productivity.start(lp)
        if self.plugins:
            self.plugins.start_watcher(lp)
        logger.info("Skill router started.")

    def stop(self):
        if self.plugins:
            self.plugins.stop_watcher()

    async def handle(self, command: str) -> Optional[str]:
        """
        Try each skill in order. Return the first response, or None
        to let the AI Brain handle it.
        """
        if not self.agent_enabled:
            return None

        for name, handler in self._skills:
            try:
                result = handler(command)
                if asyncio.iscoroutine(result):
                    result = await result
                if result is not None:
                    logger.info(f"[{name}] handled: {command[:50]}")
                    return result
            except Exception as e:
                logger.error(f"Skill '{name}' error: {e}")
        return None

    async def _reminder_alert(self, reminder: dict):
        """Called when a reminder/alarm fires. Speaks via TTS if available."""
        title = reminder.get("title", "Reminder")
        logger.info(f"🔔 ALARM: {title}")
        # Try to speak it
        try:
            from voice.tts import TTS
            tts = TTS()
            await tts.speak(f"Reminder: {title}")
        except Exception:
            pass
        # Also print to UI via server if running
        try:
            import aiohttp
            async with aiohttp.ClientSession() as s:
                await s.post("http://127.0.0.1:7771/reminder_alert",
                             json={"title": title}, timeout=aiohttp.ClientTimeout(total=2))
        except Exception:
            pass

    def status(self) -> dict:
        available = [name for name, _ in self._skills]
        return {
            "skills": available,
            "plugins": self.plugins.status() if self.plugins else None,
        }
