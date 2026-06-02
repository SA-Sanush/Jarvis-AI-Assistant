"""
JARVIS PC Control — skills/pc_control.py
Natural language → system actions.
Handles: app launching, file ops, system controls, window management,
         screenshots, clipboard, scheduling, and more.
"""

import re
import logging
import asyncio
import platform
from typing import Optional

from .os_layer import (
    open_application, open_url, open_file,
    list_processes, kill_process,
    get_system_info, get_battery,
    set_volume, set_brightness,
    take_screenshot, type_text, press_key,
    get_mouse_position, move_mouse, click_mouse, drag_mouse, scroll_mouse,
    get_active_window, get_clipboard, set_clipboard,
    shutdown, restart, sleep_system, lock_screen,
    run_command, OS
)
from .file_manager import FileManager

logger = logging.getLogger("jarvis.pc_control")


class PCControl:
    """
    JARVIS PC Control skill.
    Parses intent from natural language and executes OS commands.
    """

    DEFAULT_ACCESS = {
        "enabled": True,
        "files": True,
        "commands": True,
        "mouse_keyboard": True,
        "screen": True,
        "microphone": True,
        "webcam": True,
        "agent_control": True,
    }

    def __init__(self, access_config: dict = None):
        self.files = FileManager()
        self._scheduled_tasks: list = []
        self.access = {**self.DEFAULT_ACCESS, **(access_config or {})}

    # ── Intent router ──────────────────────────

    async def handle(self, command: str) -> str:
        """
        Main entry — route natural language command to the right handler.
        Returns a human-readable response string.
        """
        cmd = command.lower().strip()

        # ── Access status ──
        if _any(cmd, ["system access status", "permission status", "permissions status", "full access status"]):
            return self._access_status()

        # ── Terminal / shell command control ──
        if m := _match(cmd, r"^(?:run|execute|terminal|shell|system)\s+command:\s+(.+)"):
            return await self._run_terminal(m.group(1).strip())

        if m := _match(cmd, r"^(?:shell|terminal):\s*(.+)"):
            return await self._run_terminal(m.group(1).strip())

        # ── Web ──
        if m := _match(cmd, r"(?:go to|open|visit|navigate to)\s+((?:https?://)?[\w\-\.]+\.\w{2,})"):
            return await self._open_url(m.group(1).strip())

        if m := _match(cmd, r"search\s+(?:for\s+)?(.+)\s+(?:on|in)\s+(google|youtube|bing|github|reddit)"):
            return await self._web_search(m.group(1).strip(), m.group(2).strip())

        if m := _match(cmd, r"(?:google|search for|look up)\s+(.+)"):
            return await self._web_search(m.group(1).strip(), "google")

        # ── Files ──
        if m := _match(command, r"(?i)^(?:read|cat|show contents of)\s+(?:file\s+)?(.+)$"):
            return await self._read_file(_clean_path(m.group(1)))

        if m := _match(command, r"(?i)^(?:write|create)\s+(?:file\s+)?(.+?)\s+(?:with|containing|content:)\s*(.+)$"):
            return await self._write_file(_clean_path(m.group(1)), m.group(2), append=False)

        if m := _match(command, r"(?i)^append\s+(?:to\s+)?(?:file\s+)?(.+?)\s+(?:with|content:)\s*(.+)$"):
            return await self._write_file(_clean_path(m.group(1)), m.group(2), append=True)

        if m := _match(command, r"(?i)^(?:create|make)\s+(?:folder|directory)\s+(.+)$"):
            return await self._create_folder(_clean_path(m.group(1)))

        if m := _match(command, r"(?i)^copy\s+(?:file|folder|directory)?\s*(.+?)\s+to\s+(.+)$"):
            return await self._copy_path(_clean_path(m.group(1)), _clean_path(m.group(2)))

        if m := _match(command, r"(?i)^(?:move|cut)\s+(?:file|folder|directory)?\s*(.+?)\s+to\s+(.+)$"):
            return await self._move_path(_clean_path(m.group(1)), _clean_path(m.group(2)))

        if m := _match(command, r"(?i)^rename\s+(?:file|folder|directory)?\s*(.+?)\s+to\s+(.+)$"):
            return await self._rename_path(_clean_path(m.group(1)), _clean_path(m.group(2)))

        if m := _match(command, r"(?i)^(permanently\s+delete|delete|trash|remove)\s+(?:file|folder|directory)?\s*(.+)$"):
            action = m.group(1).lower()
            return await self._delete_path(_clean_path(m.group(2)), permanent=action.startswith("permanently"))

        if m := _match(cmd, r"(?:find|search for|locate)\s+(?:file\s+)?(?:called\s+)?(.+)"):
            return await self._find_file(m.group(1).strip())

        if m := _match(command, r"(?i)^(?:open|show)\s+(?:file\s+)?(.+\.[\w]{1,8})$"):
            return await self._open_file(m.group(1).strip())

        if m := _match(cmd, r"organize\s+(?:my\s+)?(?:downloads|desktop|documents|folder)"):
            return await self._organize_downloads()

        if m := _match(cmd, r"(?:list|show|what's in)\s+(.+)\s+(?:folder|directory)"):
            return await self._list_dir(m.group(1).strip())

        if m := _match(cmd, r"(?:disk|storage|drive)\s+(?:space|usage|info)"):
            return await self._disk_info()

        # ── System info ──
        if _any(cmd, ["system info", "system status", "how's the system", "cpu usage", "ram usage", "performance"]):
            return await self._system_info()

        if _any(cmd, ["battery", "battery level", "charge"]):
            return self._battery_info()

        if m := _match(cmd, r"(?:list|show|what)\s+processes|running apps|running programs"):
            return await self._list_processes()

        # ── Volume / Brightness ──
        if m := _match(cmd, r"(?:set|turn)\s+volume\s+(?:to\s+)?(\d+)"):
            return await self._set_volume(int(m.group(1)))

        if _any(cmd, ["mute", "silence", "quiet"]):
            return await self._set_volume(0)

        if _any(cmd, ["unmute", "volume up", "louder"]):
            return await self._set_volume(70)

        if m := _match(cmd, r"(?:set|turn)\s+brightness\s+(?:to\s+)?(\d+)"):
            return await self._set_brightness(int(m.group(1)))

        # ── Screenshot ──
        if _any(cmd, ["screenshot", "screen capture", "capture screen", "take a screenshot"]):
            return await self._screenshot()

        # ── Clipboard ──
        if _any(cmd, ["what's in my clipboard", "clipboard content", "what did i copy"]):
            return await self._get_clipboard()

        if m := _match(cmd, r"copy\s+['\"](.+)['\"]|copy\s+this:\s+(.+)"):
            text = m.group(1) or m.group(2)
            await set_clipboard(text)
            return f"Copied to clipboard: \"{text}\""

        # ── Window management ──
        if _any(cmd, ["what window", "active window", "current window", "what's open"]):
            return await self._active_window()

        if _any(cmd, ["minimize all", "show desktop"]):
            await press_key("super+d" if OS == "Linux" else "win+d")
            return "Minimized all windows."

        if _any(cmd, ["switch window", "alt tab", "next window"]):
            await press_key("alt+tab")
            return "Switched window."

        # ── Mouse / keyboard ──
        if _any(cmd, ["mouse position", "cursor position", "where is my mouse"]):
            return await self._mouse_position()

        if m := _match(cmd, r"(?:move\s+mouse|move\s+cursor)\s+(?:to\s+)?(\d+)[,\s]+(\d+)"):
            return await self._move_mouse(int(m.group(1)), int(m.group(2)))

        if m := _match(cmd, r"(right\s+click|double\s+click|click)(?:\s+(?:at|on)\s+(\d+)[,\s]+(\d+))?"):
            action = m.group(1)
            button = "right" if "right" in action else "left"
            clicks = 2 if "double" in action else 1
            x = int(m.group(2)) if m.group(2) else None
            y = int(m.group(3)) if m.group(3) else None
            return await self._click_mouse(x, y, button=button, clicks=clicks)

        if m := _match(cmd, r"drag\s+(?:mouse\s+)?(?:to\s+)?(\d+)[,\s]+(\d+)"):
            return await self._drag_mouse(int(m.group(1)), int(m.group(2)))

        if m := _match(cmd, r"scroll\s+(up|down)(?:\s+(\d+))?"):
            direction = m.group(1)
            amount = int(m.group(2) or 5) * 120
            return await self._scroll_mouse(amount if direction == "up" else -amount)

        if m := _match(command, r"(?i)^(?:press|hit)\s+(?:key\s+)?(.+)$"):
            key = _normalize_key(m.group(1))
            return await self._press_key(key)

        # ── Power ──
        if _any(cmd, ["lock", "lock screen", "lock computer"]):
            result = await lock_screen()
            return "Screen locked." if result["success"] else "Could not lock screen."

        if _any(cmd, ["sleep", "hibernate", "suspend"]):
            await sleep_system()
            return "Going to sleep."

        if _any(cmd, ["restart", "reboot"]):
            return await self._confirm_power("restart")

        if _any(cmd, ["shutdown", "turn off", "power off"]):
            return await self._confirm_power("shutdown")

        # ── Typing ──
        if m := _match(cmd, r"type\s+['\"](.+)['\"]|type this:\s+(.+)"):
            text = m.group(1) or m.group(2)
            await type_text(text)
            return f"Typed: \"{text}\""

        # ── App control ──
        if m := _match(cmd, r"open\s+(.+)|launch\s+(.+)|start\s+(.+)|run\s+(.+)"):
            app = m.group(1) or m.group(2) or m.group(3) or m.group(4)
            return await self._open_app(app.strip())

        if m := _match(cmd, r"(?:close|kill|stop)\s+(.+)"):
            return await self._kill_process(m.group(1).strip())

        return None  # Not a PC control command, pass to AI brain

    # ── Handlers ──────────────────────────────

    async def _open_app(self, app: str) -> str:
        result = await open_application(app)
        if result["success"]:
            return f"Opening {app}."
        return f"I couldn't find '{app}'. Make sure it's installed."

    async def _kill_process(self, name: str) -> str:
        result = await kill_process(name)
        if result["success"]:
            return result["stdout"]
        return f"Couldn't close '{name}': {result['stderr']}"

    async def _open_url(self, url: str) -> str:
        await open_url(url)
        return f"Opening {url} in your browser."

    async def _web_search(self, query: str, engine: str = "google") -> str:
        urls = {
            "google": f"https://www.google.com/search?q={query.replace(' ', '+')}",
            "youtube": f"https://www.youtube.com/results?search_query={query.replace(' ', '+')}",
            "bing": f"https://www.bing.com/search?q={query.replace(' ', '+')}",
            "github": f"https://github.com/search?q={query.replace(' ', '+')}",
            "reddit": f"https://www.reddit.com/search/?q={query.replace(' ', '+')}"
        }
        url = urls.get(engine, urls["google"])
        await open_url(url)
        return f"Searching {engine} for '{query}'."

    async def _find_file(self, query: str) -> str:
        results = await self.files.find(query, max_results=5)
        if not results:
            return f"No files found matching '{query}'."
        lines = [f"Found {len(results)} file(s) matching '{query}':"]
        for f in results:
            lines.append(f"  • {f.name} — {f.path}")
        return "\n".join(lines)

    async def _open_file(self, path: str) -> str:
        # First try exact path, then search
        from pathlib import Path
        p = Path(path).expanduser()
        if not p.exists():
            results = await self.files.find(path, max_results=1)
            if results:
                path = results[0].path
            else:
                return f"File not found: {path}"
        result = await open_file(path)
        return f"Opening {path}." if result["success"] else f"Could not open {path}."

    async def _read_file(self, path: str) -> str:
        if not self._allowed("files"):
            return "File access is disabled in system_access settings."
        content = await self.files.read(path, max_chars=8000)
        return f"Contents of {path}:\n{content}"

    async def _write_file(self, path: str, content: str, append: bool = False) -> str:
        if not self._allowed("files"):
            return "File access is disabled in system_access settings."
        result = await self.files.write(path, content, append=append)
        action = "Appended to" if append else "Wrote"
        return f"{action} {result['bytes']} bytes to {result['path']}." if result["success"] else f"Could not write file: {result['error']}"

    async def _create_folder(self, path: str) -> str:
        if not self._allowed("files"):
            return "File access is disabled in system_access settings."
        result = await self.files.create_folder(path)
        return f"Created folder: {result['path']}." if result["success"] else f"Could not create folder: {result['error']}"

    async def _copy_path(self, src: str, dst: str) -> str:
        if not self._allowed("files"):
            return "File access is disabled in system_access settings."
        result = await self.files.copy(src, dst)
        return f"Copied {result['src']} to {result['dst']}." if result["success"] else f"Could not copy: {result['error']}"

    async def _move_path(self, src: str, dst: str) -> str:
        if not self._allowed("files"):
            return "File access is disabled in system_access settings."
        result = await self.files.move(src, dst)
        return f"Moved {result['src']} to {result['dst']}." if result["success"] else f"Could not move: {result['error']}"

    async def _rename_path(self, path: str, new_name: str) -> str:
        if not self._allowed("files"):
            return "File access is disabled in system_access settings."
        result = await self.files.rename(path, new_name)
        return f"Renamed {result['old']} to {result['new']}." if result["success"] else f"Could not rename: {result['error']}"

    async def _delete_path(self, path: str, permanent: bool = False) -> str:
        if not self._allowed("files"):
            return "File access is disabled in system_access settings."
        result = await self.files.delete(path, trash=not permanent)
        if result["success"]:
            action = "Permanently deleted" if permanent else "Moved to trash"
            return f"{action}: {result['path']}."
        return f"Could not delete: {result['error']}"

    async def _organize_downloads(self) -> str:
        result = await self.files.organize_folder()
        if result["success"]:
            lines = [f"Organized {result['moved']} files:"]
            for cat, names in result["by_category"].items():
                lines.append(f"  • {cat.capitalize()}: {len(names)} files")
            return "\n".join(lines)
        return "Could not organize folder."

    async def _list_dir(self, path: str) -> str:
        from .os_layer import get_home, get_downloads, get_documents
        path_map = {
            "home": str(get_home()),
            "downloads": str(get_downloads()),
            "documents": str(get_documents()),
            "desktop": str(get_home() / "Desktop"),
        }
        real_path = path_map.get(path.lower(), path)
        items = await self.files.list_dir(real_path)
        if not items:
            return f"No files found in {real_path}."
        lines = [f"Contents of {real_path} ({len(items)} items):"]
        for item in items[:15]:
            icon = "📁" if item.is_dir else "📄"
            lines.append(f"  {icon} {item.name}")
        if len(items) > 15:
            lines.append(f"  ... and {len(items) - 15} more")
        return "\n".join(lines)

    async def _disk_info(self) -> str:
        info = await self.files.disk_usage("/" if OS == "Linux" else "C:\\")
        return (f"Disk usage: {info['used_gb']} GB used of {info['total_gb']} GB "
                f"({info['percent_used']}% full). {info['free_gb']} GB free.")

    async def _system_info(self) -> str:
        info = get_system_info()
        if "error" in info:
            return f"Could not get system info: {info['error']}"
        return (f"System status:\n"
                f"  CPU: {info['cpu_percent']}%\n"
                f"  RAM: {info['ram_used_gb']} / {info['ram_total_gb']} GB ({info['ram_percent']}%)\n"
                f"  Disk: {info['disk_used_gb']} / {info['disk_total_gb']} GB ({info['disk_percent']}%)")

    def _battery_info(self) -> str:
        b = get_battery()
        if not b:
            return "No battery detected (desktop system or battery info unavailable)."
        status = "charging" if b["plugged"] else "on battery"
        mins = b.get("secs_left", 0) // 60
        time_left = f", ~{mins} minutes remaining" if mins > 0 and not b["plugged"] else ""
        return f"Battery: {b['percent']:.0f}% ({status}{time_left})."

    async def _list_processes(self) -> str:
        procs = list_processes()[:10]
        if not procs:
            return "Could not retrieve process list."
        lines = ["Top processes by CPU usage:"]
        for p in procs:
            lines.append(f"  [{p['pid']}] {p['name']} — CPU: {p.get('cpu_percent', 0):.1f}%  RAM: {p.get('memory_percent', 0):.1f}%")
        return "\n".join(lines)

    async def _set_volume(self, level: int) -> str:
        await set_volume(level)
        return f"Volume set to {level}%." if level > 0 else "Muted."

    async def _set_brightness(self, level: int) -> str:
        result = await set_brightness(level)
        return f"Brightness set to {level}%." if result["success"] else "Could not change brightness."

    async def _screenshot(self) -> str:
        path = await take_screenshot()
        return f"Screenshot saved: {path}"

    async def _get_clipboard(self) -> str:
        text = await get_clipboard()
        if not text:
            return "Clipboard is empty."
        preview = text[:200] + ("..." if len(text) > 200 else "")
        return f"Clipboard contains:\n{preview}"

    async def _active_window(self) -> str:
        title = await get_active_window()
        return f"Active window: {title}" if title else "Could not detect active window."

    async def _run_terminal(self, cmd: str) -> str:
        if not self._allowed("commands"):
            return "System command access is disabled in system_access settings."
        result = await run_command(cmd, timeout=15)
        output = result["stdout"] or result["stderr"] or "(no output)"
        status = "✓" if result["success"] else "✗"
        return f"[{status}] $ {cmd}\n{output[:500]}"

    async def _mouse_position(self) -> str:
        if not self._allowed("mouse_keyboard"):
            return "Mouse and keyboard access is disabled in system_access settings."
        result = await get_mouse_position()
        return f"Mouse position: {result['x']}, {result['y']}." if result["success"] else f"Could not read mouse position: {result['error']}"

    async def _move_mouse(self, x: int, y: int) -> str:
        if not self._allowed("mouse_keyboard"):
            return "Mouse and keyboard access is disabled in system_access settings."
        result = await move_mouse(x, y)
        return f"Moved mouse to {x}, {y}." if result["success"] else f"Could not move mouse: {result['error']}"

    async def _click_mouse(self, x: int = None, y: int = None, button: str = "left", clicks: int = 1) -> str:
        if not self._allowed("mouse_keyboard"):
            return "Mouse and keyboard access is disabled in system_access settings."
        result = await click_mouse(x, y, button=button, clicks=clicks)
        if result["success"]:
            location = f" at {x}, {y}" if x is not None and y is not None else ""
            return f"{button.capitalize()} clicked{location}."
        return f"Could not click mouse: {result['error']}"

    async def _drag_mouse(self, x: int, y: int) -> str:
        if not self._allowed("mouse_keyboard"):
            return "Mouse and keyboard access is disabled in system_access settings."
        result = await drag_mouse(x, y)
        return f"Dragged mouse to {x}, {y}." if result["success"] else f"Could not drag mouse: {result['error']}"

    async def _scroll_mouse(self, amount: int) -> str:
        if not self._allowed("mouse_keyboard"):
            return "Mouse and keyboard access is disabled in system_access settings."
        result = await scroll_mouse(amount)
        direction = "up" if amount > 0 else "down"
        return f"Scrolled {direction}." if result["success"] else f"Could not scroll: {result['error']}"

    async def _press_key(self, key: str) -> str:
        if not self._allowed("mouse_keyboard"):
            return "Mouse and keyboard access is disabled in system_access settings."
        await press_key(key)
        return f"Pressed {key}."

    def _allowed(self, capability: str) -> bool:
        return bool(self.access.get("enabled", True) and self.access.get(capability, True))

    def _access_status(self) -> str:
        labels = {
            "files": "Files and folders",
            "commands": "System commands",
            "mouse_keyboard": "Mouse and keyboard",
            "screen": "Screen access",
            "microphone": "Microphone",
            "webcam": "Webcam",
            "agent_control": "AI agent control",
        }
        lines = ["JARVIS system access:"]
        for key, label in labels.items():
            lines.append(f"  {'ON ' if self._allowed(key) else 'OFF'} {label}")
        lines.append("  Note: Linux may still require OS/device permissions and installed packages.")
        return "\n".join(lines)

    async def _confirm_power(self, action: str) -> str:
        # In voice/chat mode, add a confirmation step
        return f"Are you sure you want to {action}? Say 'yes confirm {action}' to proceed."

    async def confirm_power_action(self, action: str) -> str:
        if action == "restart":
            await restart()
            return "Restarting now."
        elif action == "shutdown":
            await shutdown()
            return "Shutting down."
        return "Unknown power action."


# ── Helpers ────────────────────────────────

def _match(text: str, pattern: str):
    return re.search(pattern, text, re.IGNORECASE)

def _any(text: str, keywords: list) -> bool:
    return any(kw in text for kw in keywords)

def _clean_path(path: str) -> str:
    """Trim quotes and natural-language filler around a path."""
    path = path.strip()
    if (path.startswith('"') and path.endswith('"')) or (path.startswith("'") and path.endswith("'")):
        path = path[1:-1]
    return path.strip()

def _normalize_key(key: str) -> str:
    key = key.strip().lower()
    replacements = {
        "control": "ctrl",
        "command": "cmd",
        "windows": "win",
        "space bar": "space",
        "escape": "esc",
        "return": "enter",
    }
    for old, new in replacements.items():
        key = key.replace(old, new)
    key = key.replace(" plus ", "+").replace(" and ", "+")
    key = re.sub(r"\s*\+\s*", "+", key)
    return key
