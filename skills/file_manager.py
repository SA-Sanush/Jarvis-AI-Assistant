"""
JARVIS File Manager — skills/file_manager.py
Full file system control: search, read, write, move, organize, watch.
Works identically on Windows and Fedora.
"""

import os
import re
import time
import shutil
import hashlib
import logging
import asyncio
import mimetypes
from pathlib import Path
from datetime import datetime
from typing import Optional, AsyncIterator
from dataclasses import dataclass

logger = logging.getLogger("jarvis.files")


@dataclass
class FileInfo:
    path: str
    name: str
    size_bytes: int
    modified: float
    is_dir: bool
    mime_type: str

    @property
    def size_human(self) -> str:
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if self.size_bytes < 1024:
                return f"{self.size_bytes:.1f} {unit}"
            self.size_bytes /= 1024
        return f"{self.size_bytes:.1f} PB"

    @property
    def modified_str(self) -> str:
        return datetime.fromtimestamp(self.modified).strftime("%Y-%m-%d %H:%M")


class FileManager:
    """
    JARVIS File Manager.
    All operations are async and cross-platform.
    """

    # File categories for smart organization
    CATEGORIES = {
        "images":     [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".ico", ".tiff"],
        "videos":     [".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v"],
        "audio":      [".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".wma"],
        "documents":  [".pdf", ".doc", ".docx", ".odt", ".txt", ".rtf", ".md"],
        "spreadsheets": [".xls", ".xlsx", ".csv", ".ods"],
        "code":       [".py", ".js", ".ts", ".html", ".css", ".java", ".cpp", ".c", ".rs", ".go"],
        "archives":   [".zip", ".tar", ".gz", ".rar", ".7z", ".bz2"],
        "executables": [".exe", ".msi", ".sh", ".deb", ".rpm", ".AppImage"],
    }

    def __init__(self):
        pass

    # ── Search ─────────────────────────────────

    async def find(
        self,
        query: str,
        search_in: str = None,
        max_results: int = 20,
        file_type: str = None,        # "images", "documents", etc.
        recursive: bool = True
    ) -> list[FileInfo]:
        """Find files by name pattern or content hint."""
        base = Path(search_in).expanduser() if search_in else Path.home()
        query_lower = query.lower()
        results = []

        exts = self.CATEGORIES.get(file_type, []) if file_type else []

        def _search(directory: Path, depth: int = 0):
            if depth > 8 or len(results) >= max_results:
                return
            try:
                for item in directory.iterdir():
                    if item.name.startswith("."):
                        continue
                    if item.is_dir() and recursive:
                        _search(item, depth + 1)
                    elif item.is_file():
                        name_match = query_lower in item.name.lower()
                        ext_match = not exts or item.suffix.lower() in exts
                        if name_match and ext_match:
                            results.append(self._stat(item))
                            if len(results) >= max_results:
                                return
            except PermissionError:
                pass

        await asyncio.to_thread(_search, base)
        return results

    async def find_recent(self, days: int = 7, directory: str = None) -> list[FileInfo]:
        """Find recently modified files."""
        base = Path(directory).expanduser() if directory else Path.home()
        cutoff = time.time() - (days * 86400)
        results = []

        def _walk(d: Path):
            try:
                for item in d.iterdir():
                    if item.name.startswith("."):
                        continue
                    if item.is_dir():
                        _walk(item)
                    elif item.is_file() and item.stat().st_mtime > cutoff:
                        results.append(self._stat(item))
            except PermissionError:
                pass

        await asyncio.to_thread(_walk, base)
        return sorted(results, key=lambda f: f.modified, reverse=True)[:50]

    async def find_large(self, min_mb: float = 100, directory: str = None) -> list[FileInfo]:
        """Find large files."""
        base = Path(directory).expanduser() if directory else Path.home()
        min_bytes = min_mb * 1024 * 1024
        results = []

        def _walk(d: Path):
            try:
                for item in d.iterdir():
                    if item.name.startswith("."):
                        continue
                    if item.is_dir():
                        _walk(item)
                    elif item.is_file():
                        try:
                            s = item.stat()
                            if s.st_size >= min_bytes:
                                results.append(self._stat(item))
                        except Exception:
                            pass
            except PermissionError:
                pass

        await asyncio.to_thread(_walk, base)
        return sorted(results, key=lambda f: f.size_bytes, reverse=True)

    # ── Read / Write ───────────────────────────

    async def read(self, path: str, max_chars: int = 10000) -> str:
        """Read a text file."""
        p = Path(path).expanduser()
        try:
            content = await asyncio.to_thread(p.read_text, errors="replace")
            return content[:max_chars]
        except Exception as e:
            return f"Error reading file: {e}"

    async def write(self, path: str, content: str, append: bool = False) -> dict:
        """Write or append to a text file."""
        p = Path(path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            mode = "a" if append else "w"
            def _write():
                with p.open(mode, encoding="utf-8") as f:
                    return f.write(content)
            written = await asyncio.to_thread(_write)
            return {"success": True, "path": str(p), "bytes": written}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def append(self, path: str, content: str) -> dict:
        return await self.write(path, content, append=True)

    # ── File operations ────────────────────────

    async def copy(self, src: str, dst: str) -> dict:
        src_p, dst_p = Path(src).expanduser(), Path(dst).expanduser()
        try:
            dst_p.parent.mkdir(parents=True, exist_ok=True)
            if src_p.is_dir():
                await asyncio.to_thread(shutil.copytree, src_p, dst_p)
            else:
                await asyncio.to_thread(shutil.copy2, src_p, dst_p)
            return {"success": True, "src": str(src_p), "dst": str(dst_p)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def move(self, src: str, dst: str) -> dict:
        src_p, dst_p = Path(src).expanduser(), Path(dst).expanduser()
        try:
            dst_p.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(shutil.move, str(src_p), str(dst_p))
            return {"success": True, "src": str(src_p), "dst": str(dst_p)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def delete(self, path: str, trash: bool = True) -> dict:
        """Delete a file (moves to trash by default)."""
        p = Path(path).expanduser()
        try:
            if trash:
                try:
                    import send2trash
                    await asyncio.to_thread(send2trash.send2trash, str(p))
                    return {"success": True, "action": "trashed", "path": str(p)}
                except ImportError:
                    pass  # Fall through to permanent delete

            if p.is_dir():
                await asyncio.to_thread(shutil.rmtree, p)
            else:
                await asyncio.to_thread(p.unlink)
            return {"success": True, "action": "deleted", "path": str(p)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def rename(self, path: str, new_name: str) -> dict:
        p = Path(path).expanduser()
        new_p = p.parent / new_name
        try:
            await asyncio.to_thread(p.rename, new_p)
            return {"success": True, "old": str(p), "new": str(new_p)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def create_folder(self, path: str) -> dict:
        p = Path(path).expanduser()
        try:
            await asyncio.to_thread(p.mkdir, parents=True, exist_ok=True)
            return {"success": True, "path": str(p)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def list_dir(self, path: str = "~") -> list[FileInfo]:
        p = Path(path).expanduser()
        try:
            items = []
            for item in p.iterdir():
                if not item.name.startswith("."):
                    items.append(self._stat(item))
            return sorted(items, key=lambda x: (not x.is_dir, x.name.lower()))
        except Exception:
            return []

    # ── Smart organize ─────────────────────────

    async def organize_folder(self, directory: str = None) -> dict:
        """
        Auto-organize a messy folder by file type.
        Creates subfolders: Images/, Videos/, Documents/, etc.
        """
        base = Path(directory or "~/Downloads").expanduser()
        moved = {}

        async def _move_file(item: Path):
            ext = item.suffix.lower()
            category = next(
                (cat for cat, exts in self.CATEGORIES.items() if ext in exts),
                "other"
            )
            dest_dir = base / category.capitalize()
            dest_dir.mkdir(exist_ok=True)
            dest = dest_dir / item.name
            # Avoid overwriting
            counter = 1
            while dest.exists():
                dest = dest_dir / f"{item.stem}_{counter}{item.suffix}"
                counter += 1
            await asyncio.to_thread(shutil.move, str(item), str(dest))
            moved.setdefault(category, []).append(item.name)

        tasks = []
        for item in base.iterdir():
            if item.is_file() and not item.name.startswith("."):
                tasks.append(_move_file(item))

        await asyncio.gather(*tasks, return_exceptions=True)
        total = sum(len(v) for v in moved.values())
        return {"success": True, "moved": total, "by_category": moved}

    async def find_duplicates(self, directory: str = None) -> dict:
        """Find duplicate files by content hash."""
        base = Path(directory or "~/Downloads").expanduser()
        hashes: dict[str, list[str]] = {}

        def _hash_file(p: Path) -> str:
            h = hashlib.md5()
            with open(p, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    h.update(chunk)
            return h.hexdigest()

        def _walk(d: Path):
            for item in d.iterdir():
                if item.is_file():
                    try:
                        digest = _hash_file(item)
                        hashes.setdefault(digest, []).append(str(item))
                    except Exception:
                        pass
                elif item.is_dir():
                    _walk(item)

        await asyncio.to_thread(_walk, base)
        duplicates = {h: paths for h, paths in hashes.items() if len(paths) > 1}
        return {"found": len(duplicates), "groups": duplicates}

    # ── Disk usage ─────────────────────────────

    async def disk_usage(self, path: str = "/") -> dict:
        """Get disk usage stats."""
        try:
            usage = await asyncio.to_thread(shutil.disk_usage, path)
            return {
                "total_gb": round(usage.total / 1e9, 2),
                "used_gb": round(usage.used / 1e9, 2),
                "free_gb": round(usage.free / 1e9, 2),
                "percent_used": round(usage.used / usage.total * 100, 1)
            }
        except Exception as e:
            return {"error": str(e)}

    async def folder_size(self, path: str) -> int:
        """Get total size of a folder in bytes."""
        p = Path(path).expanduser()
        total = 0
        def _calc(d: Path):
            nonlocal total
            for item in d.rglob("*"):
                try:
                    if item.is_file():
                        total += item.stat().st_size
                except Exception:
                    pass
        await asyncio.to_thread(_calc, p)
        return total

    # ── File watcher ───────────────────────────

    async def watch(self, directory: str, callback, recursive: bool = True):
        """Watch a directory for changes and call callback(event)."""
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler

            class Handler(FileSystemEventHandler):
                def on_any_event(self, event):
                    asyncio.run_coroutine_threadsafe(
                        callback(event), asyncio.get_event_loop()
                    )

            observer = Observer()
            observer.schedule(Handler(), str(Path(directory).expanduser()), recursive=recursive)
            observer.start()
            logger.info(f"Watching: {directory}")
            return observer
        except ImportError:
            logger.error("watchdog not installed: pip install watchdog")
            return None

    # ── Helpers ────────────────────────────────

    def _stat(self, p: Path) -> FileInfo:
        try:
            s = p.stat()
            mime, _ = mimetypes.guess_type(str(p))
            return FileInfo(
                path=str(p),
                name=p.name,
                size_bytes=s.st_size,
                modified=s.st_mtime,
                is_dir=p.is_dir(),
                mime_type=mime or ("inode/directory" if p.is_dir() else "application/octet-stream")
            )
        except Exception:
            return FileInfo(str(p), p.name, 0, 0, p.is_dir(), "")

    def categorize(self, filename: str) -> str:
        ext = Path(filename).suffix.lower()
        return next((cat for cat, exts in self.CATEGORIES.items() if ext in exts), "other")
