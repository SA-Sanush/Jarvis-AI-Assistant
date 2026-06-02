"""
JARVIS OS Layer — skills/os_layer.py
Cross-platform abstraction for Windows and Fedora Linux.
All platform-specific code lives here. The rest of the system uses this API.
"""

import os
import sys
import time
import shutil
import logging
import asyncio
import platform
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger("jarvis.os")

OS = platform.system()          # "Windows" or "Linux"
DISTRO = ""

if OS == "Linux":
    try:
        import distro
        DISTRO = distro.id()    # "fedora", "ubuntu", "debian", etc.
    except ImportError:
        DISTRO = "linux"


# ─────────────────────────────────────────────
# Core OS utilities
# ─────────────────────────────────────────────

def get_os() -> dict:
    return {
        "system": OS,
        "distro": DISTRO,
        "release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
    }


async def run_command(cmd: str | list, shell: bool = True, timeout: int = 30) -> dict:
    """Run a shell command and return stdout/stderr."""
    try:
        if isinstance(cmd, list):
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
        else:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return {
            "stdout": stdout.decode(errors="replace").strip(),
            "stderr": stderr.decode(errors="replace").strip(),
            "returncode": proc.returncode,
            "success": proc.returncode == 0
        }
    except asyncio.TimeoutError:
        return {"stdout": "", "stderr": "Command timed out", "returncode": -1, "success": False}
    except Exception as e:
        return {"stdout": "", "stderr": str(e), "returncode": -1, "success": False}


def get_home() -> Path:
    return Path.home()


def get_desktop() -> Path:
    if OS == "Windows":
        import winreg
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                  r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders")
            desktop = winreg.QueryValueEx(key, "Desktop")[0]
            return Path(desktop)
        except Exception:
            pass
    return Path.home() / "Desktop"


def get_downloads() -> Path:
    if OS == "Windows":
        return Path.home() / "Downloads"
    return Path.home() / "Downloads"


def get_documents() -> Path:
    return Path.home() / "Documents"


# ─────────────────────────────────────────────
# Application launcher
# ─────────────────────────────────────────────

# Common app name → command mapping
APP_MAP_WINDOWS = {
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "paint": "mspaint.exe",
    "explorer": "explorer.exe",
    "task manager": "taskmgr.exe",
    "control panel": "control.exe",
    "settings": "ms-settings:",
    "chrome": "chrome",
    "firefox": "firefox",
    "edge": "msedge",
    "spotify": "spotify",
    "discord": "discord",
    "vscode": "code",
    "vs code": "code",
    "terminal": "wt",             # Windows Terminal
    "cmd": "cmd.exe",
    "powershell": "powershell.exe",
    "word": "winword",
    "excel": "excel",
    "powerpoint": "powerpnt",
    "outlook": "outlook",
    "steam": "steam",
    "vlc": "vlc",
    "obs": "obs64",
}

APP_MAP_LINUX = {
    "files": "nautilus",           # GNOME Files
    "calculator": "gnome-calculator",
    "text editor": "gedit",
    "settings": "gnome-control-center",
    "chrome": "google-chrome",
    "chromium": "chromium-browser",
    "firefox": "firefox",
    "spotify": "spotify",
    "discord": "discord",
    "vscode": "code",
    "vs code": "code",
    "terminal": "gnome-terminal",
    "konsole": "konsole",
    "steam": "steam",
    "vlc": "vlc",
    "obs": "obs",
    "gimp": "gimp",
    "libreoffice": "libreoffice",
    "writer": "libreoffice --writer",
    "calc": "libreoffice --calc",
}


async def open_application(app_name: str) -> dict:
    """Open an application by name. Cross-platform."""
    name = app_name.lower().strip()

    if OS == "Windows":
        cmd = APP_MAP_WINDOWS.get(name, name)
        result = await run_command(f'start "" "{cmd}"')
        if not result["success"]:
            # Try direct executable
            result = await run_command(cmd)

    elif OS == "Linux":
        cmd = APP_MAP_LINUX.get(name, name)
        # Launch detached so JARVIS doesn't wait
        result = await run_command(f"nohup {cmd} &>/dev/null &")
        if not result["success"]:
            # Try xdg-open or which
            which = shutil.which(cmd.split()[0])
            if which:
                result = await run_command(f"nohup {which} &>/dev/null &")

    else:
        result = {"success": False, "stderr": f"Unsupported OS: {OS}"}

    return result


async def open_url(url: str) -> dict:
    """Open a URL in the default browser."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        import webbrowser
        await asyncio.to_thread(webbrowser.open, url)
        return {"success": True, "stdout": f"Opened {url}"}
    except Exception as e:
        return {"success": False, "stderr": str(e)}


async def open_file(path: str) -> dict:
    """Open a file with its default application."""
    p = Path(path).expanduser()
    if not p.exists():
        return {"success": False, "stderr": f"File not found: {path}"}

    if OS == "Windows":
        return await run_command(f'start "" "{p}"')
    elif OS == "Linux":
        return await run_command(f'xdg-open "{p}"')
    else:
        return {"success": False, "stderr": "Unsupported OS"}


# ─────────────────────────────────────────────
# Process management
# ─────────────────────────────────────────────

def list_processes(filter_name: str = None) -> list[dict]:
    """List running processes."""
    try:
        import psutil
        procs = []
        for p in psutil.process_iter(["pid", "name", "status", "cpu_percent", "memory_percent"]):
            try:
                info = p.info
                if filter_name and filter_name.lower() not in info["name"].lower():
                    continue
                procs.append(info)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return sorted(procs, key=lambda x: x.get("cpu_percent", 0) or 0, reverse=True)
    except ImportError:
        return []


async def kill_process(name_or_pid: str | int) -> dict:
    """Kill a process by name or PID."""
    try:
        import psutil
        killed = []
        for p in psutil.process_iter(["pid", "name"]):
            try:
                if isinstance(name_or_pid, int) and p.pid == name_or_pid:
                    p.terminate()
                    killed.append(p.info["name"])
                elif isinstance(name_or_pid, str) and name_or_pid.lower() in p.info["name"].lower():
                    p.terminate()
                    killed.append(p.info["name"])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        if killed:
            return {"success": True, "stdout": f"Terminated: {', '.join(killed)}"}
        return {"success": False, "stderr": f"No process found: {name_or_pid}"}
    except ImportError:
        return {"success": False, "stderr": "psutil not installed"}


# ─────────────────────────────────────────────
# System info
# ─────────────────────────────────────────────

def get_system_info() -> dict:
    """Get CPU, RAM, disk usage."""
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=0.5)
        ram = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        net = psutil.net_io_counters()
        return {
            "cpu_percent": cpu,
            "ram_used_gb": round(ram.used / 1e9, 1),
            "ram_total_gb": round(ram.total / 1e9, 1),
            "ram_percent": ram.percent,
            "disk_used_gb": round(disk.used / 1e9, 1),
            "disk_total_gb": round(disk.total / 1e9, 1),
            "disk_percent": disk.percent,
            "net_sent_mb": round(net.bytes_sent / 1e6, 1),
            "net_recv_mb": round(net.bytes_recv / 1e6, 1),
        }
    except ImportError:
        return {"error": "psutil not installed"}


def get_battery() -> Optional[dict]:
    try:
        import psutil
        b = psutil.sensors_battery()
        if b:
            return {"percent": b.percent, "plugged": b.power_plugged,
                    "secs_left": b.secsleft}
        return None
    except Exception:
        return None


# ─────────────────────────────────────────────
# Volume / brightness control
# ─────────────────────────────────────────────

async def set_volume(level: int) -> dict:
    """Set system volume 0-100."""
    level = max(0, min(100, level))
    if OS == "Windows":
        script = f"""
        $obj = New-Object -ComObject WScript.Shell
        $obj.SendKeys([char]174 * 50)  # Mute first
        Add-Type -TypeDefinition @'
        using System.Runtime.InteropServices;
        [Guid("5CDF2C82-841E-4546-9722-0CF74078229A"),InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        interface IAudioEndpointVolume {{ int f(); int g(); int SetMasterVolumeLevelScalar(float fLevel, System.Guid pguidEventContext); }}
        [Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")] class MMDeviceEnumerator {{}}
        '@
        """
        # Simpler approach using nircmd or PowerShell
        return await run_command(
            f'powershell -c "(New-Object -ComObject WScript.Shell).SendKeys([char]175 * {level // 2})"',
        )
    elif OS == "Linux":
        # Works on PulseAudio and PipeWire
        return await run_command(f"pactl set-sink-volume @DEFAULT_SINK@ {level}%")


async def set_brightness(level: int) -> dict:
    """Set screen brightness 0-100 (Linux only via xrandr/brightnessctl)."""
    level = max(0, min(100, level))
    if OS == "Linux":
        # Try brightnessctl first
        result = await run_command(f"brightnessctl set {level}%")
        if not result["success"]:
            # xrandr fallback
            frac = level / 100.0
            result = await run_command(f"xrandr --output $(xrandr | grep ' connected' | head -1 | cut -d' ' -f1) --brightness {frac:.2f}")
        return result
    elif OS == "Windows":
        script = f"(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods).WmiSetBrightness(1,{level})"
        return await run_command(f"powershell -c \"{script}\"")


# ─────────────────────────────────────────────
# Window management (GUI automation)
# ─────────────────────────────────────────────

async def take_screenshot(save_path: str = None) -> str:
    """Take a screenshot and return the file path."""
    if not save_path:
        ts = int(time.time())
        save_path = str(get_home() / f"Pictures/jarvis_screenshot_{ts}.png")
    Path(save_path).parent.mkdir(parents=True, exist_ok=True)

    try:
        import pyautogui
        img = await asyncio.to_thread(pyautogui.screenshot)
        await asyncio.to_thread(img.save, save_path)
        return save_path
    except ImportError:
        if OS == "Linux":
            await run_command(f"scrot '{save_path}'")
            return save_path
        raise


async def type_text(text: str, interval: float = 0.03):
    """Type text at current cursor position."""
    try:
        import pyautogui
        await asyncio.to_thread(pyautogui.write, text, interval=interval)
    except ImportError:
        logger.error("pyautogui not installed")


async def press_key(key: str):
    """Press a keyboard key (e.g. 'enter', 'ctrl+c', 'alt+tab')."""
    try:
        import pyautogui
        if "+" in key:
            keys = [k.strip() for k in key.split("+")]
            await asyncio.to_thread(pyautogui.hotkey, *keys)
        else:
            await asyncio.to_thread(pyautogui.press, key)
    except ImportError:
        logger.error("pyautogui not installed")


async def get_mouse_position() -> dict:
    """Return the current mouse cursor position."""
    try:
        import pyautogui
        x, y = await asyncio.to_thread(pyautogui.position)
        return {"success": True, "x": x, "y": y}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def move_mouse(x: int, y: int, duration: float = 0.2) -> dict:
    """Move the mouse cursor to an absolute screen coordinate."""
    try:
        import pyautogui
        await asyncio.to_thread(pyautogui.moveTo, x, y, duration=duration)
        return {"success": True, "x": x, "y": y}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def click_mouse(x: int = None, y: int = None, button: str = "left", clicks: int = 1) -> dict:
    """Click the mouse, optionally at an absolute screen coordinate."""
    try:
        import pyautogui
        kwargs = {"button": button, "clicks": clicks}
        if x is not None and y is not None:
            kwargs.update({"x": x, "y": y})
        await asyncio.to_thread(pyautogui.click, **kwargs)
        return {"success": True, "button": button, "clicks": clicks, "x": x, "y": y}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def drag_mouse(x: int, y: int, duration: float = 0.4, button: str = "left") -> dict:
    """Drag the mouse from its current position to an absolute coordinate."""
    try:
        import pyautogui
        await asyncio.to_thread(pyautogui.dragTo, x, y, duration=duration, button=button)
        return {"success": True, "x": x, "y": y, "button": button}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def scroll_mouse(amount: int) -> dict:
    """Scroll the mouse wheel. Positive scrolls up, negative scrolls down."""
    try:
        import pyautogui
        await asyncio.to_thread(pyautogui.scroll, amount)
        return {"success": True, "amount": amount}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def get_active_window() -> Optional[str]:
    """Get the title of the currently focused window."""
    if OS == "Windows":
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
            return buf.value
        except Exception:
            return None
    elif OS == "Linux":
        result = await run_command("xdotool getactivewindow getwindowname")
        return result["stdout"] if result["success"] else None


# ─────────────────────────────────────────────
# Clipboard
# ─────────────────────────────────────────────

async def get_clipboard() -> str:
    try:
        import pyperclip
        return await asyncio.to_thread(pyperclip.paste)
    except Exception:
        return ""


async def set_clipboard(text: str):
    try:
        import pyperclip
        await asyncio.to_thread(pyperclip.copy, text)
    except Exception:
        pass


# ─────────────────────────────────────────────
# Power management
# ─────────────────────────────────────────────

async def shutdown(delay_seconds: int = 0) -> dict:
    if OS == "Windows":
        return await run_command(f"shutdown /s /t {delay_seconds}")
    elif OS == "Linux":
        return await run_command(f"shutdown -h +{delay_seconds // 60}" if delay_seconds else "shutdown -h now")


async def restart(delay_seconds: int = 0) -> dict:
    if OS == "Windows":
        return await run_command(f"shutdown /r /t {delay_seconds}")
    elif OS == "Linux":
        return await run_command("reboot")


async def sleep_system() -> dict:
    if OS == "Windows":
        return await run_command("rundll32.exe powrprof.dll,SetSuspendState 0,1,0")
    elif OS == "Linux":
        return await run_command("systemctl suspend")


async def lock_screen() -> dict:
    if OS == "Windows":
        return await run_command("rundll32.exe user32.dll,LockWorkStation")
    elif OS == "Linux":
        # Try multiple DE-specific commands
        for cmd in ["loginctl lock-session", "gnome-screensaver-command -l", "xdg-screensaver lock"]:
            result = await run_command(cmd)
            if result["success"]:
                return result
        return {"success": False, "stderr": "Could not lock screen"}
