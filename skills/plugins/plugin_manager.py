"""
JARVIS Plugin System — skills/plugins/plugin_manager.py
Lets JARVIS learn new skills at runtime — no restart needed.
Plugins are Python files dropped into ~/.jarvis/plugins/
Each plugin defines a handler function and a manifest.

Plugin format:
  ~/.jarvis/plugins/weather.py
  ────────────────────────────
  MANIFEST = {
      "name": "Weather",
      "description": "Get current weather for any city",
      "triggers": ["weather", "temperature", "forecast"],
      "author": "you",
      "version": "1.0.0"
  }

  async def handle(command: str, jarvis) -> Optional[str]:
      if "weather" in command.lower():
          city = extract_city(command)
          return await get_weather(city)
      return None
"""

import os
import sys
import time
import asyncio
import hashlib
import logging
import importlib
import importlib.util
import threading
from pathlib import Path
from typing import Optional, Callable, Any
from dataclasses import dataclass, field

logger = logging.getLogger("jarvis.plugins")

PLUGIN_DIR = Path("~/.jarvis/plugins").expanduser()
BUILTIN_DIR = Path(__file__).parent / "builtin"


@dataclass
class PluginInfo:
    name: str
    description: str
    triggers: list[str]
    version: str
    author: str
    file_path: str
    module_name: str
    enabled: bool = True
    error: Optional[str] = None
    loaded_at: float = field(default_factory=time.time)
    call_count: int = 0
    last_hash: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "triggers": self.triggers,
            "version": self.version,
            "author": self.author,
            "enabled": self.enabled,
            "error": self.error,
            "call_count": self.call_count,
            "file": self.file_path,
        }


class PluginManager:
    """
    JARVIS Plugin Manager.
    - Auto-discovers plugins in ~/.jarvis/plugins/
    - Hot-reloads changed plugins without restarting
    - Watches directory for new plugins
    - Provides a priority-ordered handler chain
    """

    def __init__(self, jarvis_instance=None):
        self._jarvis = jarvis_instance
        self._plugins: dict[str, PluginInfo] = {}
        self._modules: dict[str, Any] = {}
        self._watcher_thread: Optional[threading.Thread] = None
        self._running = False

        # Ensure plugin dir exists
        PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
        BUILTIN_DIR.mkdir(parents=True, exist_ok=True)

        # Add to Python path
        sys.path.insert(0, str(PLUGIN_DIR))
        sys.path.insert(0, str(BUILTIN_DIR))

    # ── Loading ────────────────────────────────────────────

    def load_all(self) -> dict:
        """Discover and load all plugins from all directories."""
        results = {"loaded": [], "failed": []}

        dirs = [PLUGIN_DIR, BUILTIN_DIR]
        for d in dirs:
            if d.exists():
                for f in d.glob("*.py"):
                    if f.name.startswith("_"):
                        continue
                    result = self.load_plugin(f)
                    if result["success"]:
                        results["loaded"].append(f.stem)
                    else:
                        results["failed"].append({"name": f.stem, "error": result["error"]})

        logger.info(f"Plugins loaded: {len(results['loaded'])} | Failed: {len(results['failed'])}")
        return results

    def load_plugin(self, path: Path) -> dict:
        """Load a single plugin file."""
        module_name = f"jarvis_plugin_{path.stem}_{hash(str(path)) & 0xFFFF:04x}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # Read manifest
            manifest = getattr(module, "MANIFEST", {})
            if not manifest:
                logger.warning(f"Plugin {path.name} has no MANIFEST — skipping.")
                return {"success": False, "error": "No MANIFEST defined"}

            if not hasattr(module, "handle"):
                return {"success": False, "error": "No handle() function defined"}

            info = PluginInfo(
                name=manifest.get("name", path.stem),
                description=manifest.get("description", ""),
                triggers=manifest.get("triggers", []),
                version=manifest.get("version", "1.0.0"),
                author=manifest.get("author", "unknown"),
                file_path=str(path),
                module_name=module_name,
                last_hash=self._file_hash(path),
            )

            self._plugins[path.stem] = info
            self._modules[path.stem] = module

            logger.info(f"Plugin loaded: {info.name} v{info.version} [{', '.join(info.triggers)}]")
            return {"success": True, "name": info.name}

        except Exception as e:
            logger.error(f"Failed to load plugin {path.name}: {e}")
            self._plugins[path.stem] = PluginInfo(
                name=path.stem, description="", triggers=[], version="",
                author="", file_path=str(path), module_name="",
                enabled=False, error=str(e)
            )
            return {"success": False, "error": str(e)}

    def reload_plugin(self, name: str) -> dict:
        """Reload a plugin (hot-reload)."""
        if name not in self._plugins:
            return {"success": False, "error": f"Plugin '{name}' not found"}
        path = Path(self._plugins[name].file_path)
        self.unload_plugin(name)
        result = self.load_plugin(path)
        if result["success"]:
            logger.info(f"Plugin hot-reloaded: {name}")
        return result

    def unload_plugin(self, name: str):
        """Remove a plugin."""
        if name in self._plugins:
            del self._plugins[name]
        if name in self._modules:
            del self._modules[name]
        logger.info(f"Plugin unloaded: {name}")

    def enable_plugin(self, name: str):
        if name in self._plugins:
            self._plugins[name].enabled = True

    def disable_plugin(self, name: str):
        if name in self._plugins:
            self._plugins[name].enabled = False

    # ── Execution ──────────────────────────────────────────

    async def handle(self, command: str) -> Optional[str]:
        """
        Try all enabled plugins in order.
        Returns first non-None result, or None if no plugin handles the command.
        """
        cmd_lower = command.lower()

        # Sort: plugins whose triggers appear in the command first
        def _priority(name: str) -> int:
            info = self._plugins[name]
            return 0 if any(t in cmd_lower for t in info.triggers) else 1

        for name in sorted(self._plugins.keys(), key=_priority):
            info = self._plugins.get(name)
            if not info or not info.enabled or info.error:
                continue

            module = self._modules.get(name)
            if not module or not hasattr(module, "handle"):
                continue

            try:
                result = module.handle(command, self._jarvis)
                if asyncio.iscoroutine(result):
                    result = await result
                if result is not None:
                    info.call_count += 1
                    logger.debug(f"Plugin '{name}' handled command.")
                    return result
            except Exception as e:
                logger.error(f"Plugin '{name}' error: {e}")
                info.error = str(e)

        return None

    # ── File watcher (hot-reload) ──────────────────────────

    def start_watcher(self, loop: asyncio.AbstractEventLoop = None):
        """Watch plugin directory for changes and auto-reload."""
        self._running = True
        self._loop = loop
        self._watcher_thread = threading.Thread(target=self._watch, daemon=True)
        self._watcher_thread.start()
        logger.info(f"Plugin watcher started on {PLUGIN_DIR}")

    def stop_watcher(self):
        self._running = False

    def _watch(self):
        known = {}
        while self._running:
            try:
                for f in PLUGIN_DIR.glob("*.py"):
                    if f.name.startswith("_"):
                        continue
                    h = self._file_hash(f)
                    if f.stem not in known:
                        # New plugin
                        known[f.stem] = h
                        self.load_plugin(f)
                        logger.info(f"New plugin detected: {f.name}")
                    elif known[f.stem] != h:
                        # Changed plugin
                        known[f.stem] = h
                        self.reload_plugin(f.stem)
                        logger.info(f"Plugin changed, hot-reloaded: {f.name}")
            except Exception as e:
                logger.error(f"Watcher error: {e}")
            time.sleep(2)

    # ── Plugin creation helper ─────────────────────────────

    def create_plugin_template(self, name: str, description: str, triggers: list[str]) -> str:
        """Generate a plugin template file."""
        template = f'''"""
JARVIS Plugin: {name}
{description}
"""
from typing import Optional

MANIFEST = {{
    "name": "{name}",
    "description": "{description}",
    "triggers": {triggers},
    "author": "you",
    "version": "1.0.0"
}}


async def handle(command: str, jarvis) -> Optional[str]:
    """
    Handle a voice/text command.
    Return a string response, or None to pass to the next handler.
    jarvis = the JARVIS instance (access brain, memory, search, pc, etc.)
    """
    cmd = command.lower()

    # Add your logic here
    if any(trigger in cmd for trigger in MANIFEST["triggers"]):
        # Example: use the AI brain
        # response = await jarvis.ask("Your prompt here")
        # return response

        return f"Plugin '{name}' triggered by: {{command}}"

    return None   # Not handled — pass to next
'''
        path = PLUGIN_DIR / f"{name.lower().replace(' ', '_')}.py"
        path.write_text(template)
        return str(path)

    # ── Status ─────────────────────────────────────────────

    def list_plugins(self) -> list[dict]:
        return [p.to_dict() for p in self._plugins.values()]

    def get_plugin(self, name: str) -> Optional[PluginInfo]:
        return self._plugins.get(name)

    def status(self) -> dict:
        total = len(self._plugins)
        active = sum(1 for p in self._plugins.values() if p.enabled and not p.error)
        return {
            "total": total, "active": active,
            "failed": total - active,
            "plugin_dir": str(PLUGIN_DIR),
            "plugins": self.list_plugins()
        }

    def _file_hash(self, path: Path) -> str:
        try:
            return hashlib.md5(path.read_bytes()).hexdigest()
        except Exception:
            return ""


# ─────────────────────────────────────────────
# Built-in example plugins
# ─────────────────────────────────────────────

def create_builtin_plugins():
    """Write built-in example plugins to the builtin directory."""
    BUILTIN_DIR.mkdir(parents=True, exist_ok=True)

    # ── Joke plugin ─────────────────────────────────────────
    (BUILTIN_DIR / "jokes.py").write_text('''"""JARVIS built-in: Jokes"""
import random
from typing import Optional

MANIFEST = {
    "name": "Jokes",
    "description": "Tell jokes on request",
    "triggers": ["joke", "funny", "make me laugh", "tell me something funny"],
    "author": "JARVIS",
    "version": "1.0.0"
}

JOKES = [
    "Why do programmers prefer dark mode? Because light attracts bugs.",
    "I told my computer I needed a break. Now it won't stop sending me Kit-Kat ads.",
    "Why was the JavaScript developer sad? Because he didn't Node how to Express himself.",
    "A SQL query walks into a bar, walks up to two tables and asks... can I join you?",
    "Why do Python programmers wear glasses? Because they can't C#.",
    "I would tell you a joke about UDP, but you might not get it.",
]

async def handle(command: str, jarvis) -> Optional[str]:
    cmd = command.lower()
    if any(t in cmd for t in MANIFEST["triggers"]):
        return random.choice(JOKES)
    return None
''')

    # ── Coin flip / dice plugin ─────────────────────────────
    (BUILTIN_DIR / "random_tools.py").write_text('''"""JARVIS built-in: Random tools"""
import random, re
from typing import Optional

MANIFEST = {
    "name": "Random Tools",
    "description": "Flip coins, roll dice, pick random numbers",
    "triggers": ["flip", "coin", "dice", "roll", "random number", "pick a number"],
    "author": "JARVIS",
    "version": "1.0.0"
}

async def handle(command: str, jarvis) -> Optional[str]:
    cmd = command.lower()
    if "flip" in cmd or "coin" in cmd:
        return f"🪙 {random.choice(['Heads', 'Tails'])}!"
    if "dice" in cmd or "roll" in cmd:
        if m := re.search(r"(\\d+)d(\\d+)", cmd):
            n, sides = int(m.group(1)), int(m.group(2))
            rolls = [random.randint(1, sides) for _ in range(min(n, 20))]
            return f"🎲 Rolled {n}d{sides}: {rolls} (total: {sum(rolls)})"
        return f"🎲 Rolled: {random.randint(1, 6)}"
    if "random number" in cmd or "pick a number" in cmd:
        if m := re.search(r"between (\\d+) and (\\d+)", cmd):
            lo, hi = int(m.group(1)), int(m.group(2))
        else:
            lo, hi = 1, 100
        return f"🔢 Random number: {random.randint(lo, hi)}"
    return None
''')

    # ── Unit converter plugin ───────────────────────────────
    (BUILTIN_DIR / "converter.py").write_text('''"""JARVIS built-in: Unit converter"""
import re
from typing import Optional

MANIFEST = {
    "name": "Unit Converter",
    "description": "Convert between units: km/miles, C/F, kg/lbs, etc.",
    "triggers": ["convert", "in miles", "in km", "in celsius", "in fahrenheit", "in kg", "in lbs"],
    "author": "JARVIS",
    "version": "1.0.0"
}

CONVERSIONS = {
    ("km", "miles"):     lambda x: x * 0.621371,
    ("miles", "km"):     lambda x: x * 1.60934,
    ("c", "f"):          lambda x: x * 9/5 + 32,
    ("celsius", "fahrenheit"): lambda x: x * 9/5 + 32,
    ("f", "c"):          lambda x: (x - 32) * 5/9,
    ("fahrenheit", "celsius"): lambda x: (x - 32) * 5/9,
    ("kg", "lbs"):       lambda x: x * 2.20462,
    ("lbs", "kg"):       lambda x: x * 0.453592,
    ("m", "ft"):         lambda x: x * 3.28084,
    ("ft", "m"):         lambda x: x * 0.3048,
    ("l", "gallons"):    lambda x: x * 0.264172,
    ("gallons", "l"):    lambda x: x * 3.78541,
}

async def handle(command: str, jarvis) -> Optional[str]:
    cmd = command.lower()
    if not any(t in cmd for t in MANIFEST["triggers"]):
        return None
    m = re.search(r"([\\d.]+)\\s*(\\w+)\\s+(?:to|in)\\s+(\\w+)", cmd)
    if not m:
        return None
    val, from_unit, to_unit = float(m.group(1)), m.group(2), m.group(3)
    key = (from_unit, to_unit)
    if key in CONVERSIONS:
        result = CONVERSIONS[key](val)
        return f"{val} {from_unit} = {result:.4g} {to_unit}"
    return f"I don\\'t know how to convert {from_unit} to {to_unit}."
''')

    logger.info(f"Built-in plugins created in {BUILTIN_DIR}")
