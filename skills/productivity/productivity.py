"""
JARVIS Productivity — skills/productivity/productivity.py
Calendar, reminders, notes, alarms, timers, todos, and scheduling.
All data stored locally in SQLite — no cloud accounts needed.
Integrates with Google Calendar / Outlook if API keys provided.
"""

import re
import json
import time
import asyncio
import logging
import sqlite3
import threading
from datetime import datetime, timedelta, date
from pathlib import Path
from dataclasses import dataclass, asdict, field
from typing import Optional, Callable

logger = logging.getLogger("jarvis.productivity")
DB_PATH = Path("~/.jarvis/productivity.db").expanduser()


# ─────────────────────────────────────────────
# Data models
# ─────────────────────────────────────────────

@dataclass
class Reminder:
    id: int
    title: str
    due: float          # Unix timestamp
    repeat: str         # "none" | "daily" | "weekly" | "monthly"
    done: bool = False
    notes: str = ""

@dataclass
class Note:
    id: int
    title: str
    content: str
    tags: list[str]
    created: float
    modified: float
    pinned: bool = False

@dataclass
class Todo:
    id: int
    task: str
    priority: str       # "low" | "medium" | "high"
    done: bool
    due: Optional[float]
    created: float
    project: str = ""

@dataclass
class CalendarEvent:
    id: int
    title: str
    start: float
    end: float
    location: str = ""
    description: str = ""
    repeat: str = "none"
    source: str = "local"   # "local" | "google" | "outlook"


# ─────────────────────────────────────────────
# Local database
# ─────────────────────────────────────────────

class ProductivityDB:
    def __init__(self, path: Path = DB_PATH):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _conn(self):
        return sqlite3.connect(self.path)

    def _init_db(self):
        with self._conn() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL, due REAL NOT NULL,
                repeat TEXT DEFAULT 'none', done INTEGER DEFAULT 0, notes TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL, content TEXT DEFAULT '',
                tags TEXT DEFAULT '[]', created REAL, modified REAL, pinned INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS todos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task TEXT NOT NULL, priority TEXT DEFAULT 'medium',
                done INTEGER DEFAULT 0, due REAL, created REAL, project TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL, start REAL NOT NULL, end REAL NOT NULL,
                location TEXT DEFAULT '', description TEXT DEFAULT '',
                repeat TEXT DEFAULT 'none', source TEXT DEFAULT 'local'
            );
            """)

    # ── Reminders ──────────────────────────────────────────

    def add_reminder(self, title: str, due: float, repeat: str = "none", notes: str = "") -> int:
        with self._conn() as c:
            cur = c.execute("INSERT INTO reminders (title, due, repeat, notes) VALUES (?,?,?,?)",
                            (title, due, repeat, notes))
            return cur.lastrowid

    def get_reminders(self, include_done: bool = False) -> list[dict]:
        with self._conn() as c:
            q = "SELECT * FROM reminders" + ("" if include_done else " WHERE done=0")
            return [dict(zip(["id","title","due","repeat","done","notes"], r))
                    for r in c.execute(q + " ORDER BY due").fetchall()]

    def mark_reminder_done(self, rid: int):
        with self._conn() as c:
            c.execute("UPDATE reminders SET done=1 WHERE id=?", (rid,))

    def get_upcoming_reminders(self, within_seconds: float = 60) -> list[dict]:
        now = time.time()
        with self._conn() as c:
            return [dict(zip(["id","title","due","repeat","done","notes"], r))
                    for r in c.execute(
                        "SELECT * FROM reminders WHERE done=0 AND due BETWEEN ? AND ?",
                        (now, now + within_seconds)
                    ).fetchall()]

    # ── Notes ──────────────────────────────────────────────

    def add_note(self, title: str, content: str = "", tags: list = None) -> int:
        now = time.time()
        with self._conn() as c:
            cur = c.execute("INSERT INTO notes (title, content, tags, created, modified) VALUES (?,?,?,?,?)",
                            (title, content, json.dumps(tags or []), now, now))
            return cur.lastrowid

    def update_note(self, nid: int, content: str = None, title: str = None):
        with self._conn() as c:
            if content is not None:
                c.execute("UPDATE notes SET content=?, modified=? WHERE id=?", (content, time.time(), nid))
            if title is not None:
                c.execute("UPDATE notes SET title=?, modified=? WHERE id=?", (title, time.time(), nid))

    def get_notes(self, tag: str = None, pinned_only: bool = False) -> list[dict]:
        with self._conn() as c:
            q = "SELECT * FROM notes"
            conditions = []
            if pinned_only:
                conditions.append("pinned=1")
            if conditions:
                q += " WHERE " + " AND ".join(conditions)
            rows = c.execute(q + " ORDER BY pinned DESC, modified DESC").fetchall()
            notes = []
            for r in rows:
                n = dict(zip(["id","title","content","tags","created","modified","pinned"], r))
                n["tags"] = json.loads(n["tags"])
                if tag and tag not in n["tags"]:
                    continue
                notes.append(n)
            return notes

    def search_notes(self, query: str) -> list[dict]:
        q = f"%{query}%"
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM notes WHERE title LIKE ? OR content LIKE ? ORDER BY modified DESC",
                (q, q)
            ).fetchall()
            return [dict(zip(["id","title","content","tags","created","modified","pinned"], r)) for r in rows]

    def delete_note(self, nid: int):
        with self._conn() as c:
            c.execute("DELETE FROM notes WHERE id=?", (nid,))

    # ── Todos ──────────────────────────────────────────────

    def add_todo(self, task: str, priority: str = "medium", due: float = None, project: str = "") -> int:
        with self._conn() as c:
            cur = c.execute("INSERT INTO todos (task, priority, due, created, project) VALUES (?,?,?,?,?)",
                            (task, priority, due, time.time(), project))
            return cur.lastrowid

    def get_todos(self, include_done: bool = False, project: str = None) -> list[dict]:
        with self._conn() as c:
            q = "SELECT * FROM todos"
            conditions = [] if include_done else ["done=0"]
            if project:
                conditions.append(f"project='{project}'")
            if conditions:
                q += " WHERE " + " AND ".join(conditions)
            order = " ORDER BY CASE priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, created"
            return [dict(zip(["id","task","priority","done","due","created","project"], r))
                    for r in c.execute(q + order).fetchall()]

    def complete_todo(self, tid: int):
        with self._conn() as c:
            c.execute("UPDATE todos SET done=1 WHERE id=?", (tid,))

    def delete_todo(self, tid: int):
        with self._conn() as c:
            c.execute("DELETE FROM todos WHERE id=?", (tid,))

    # ── Calendar ───────────────────────────────────────────

    def add_event(self, title: str, start: float, end: float = None,
                  location: str = "", description: str = "", repeat: str = "none") -> int:
        if end is None:
            end = start + 3600   # 1 hour default
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO events (title, start, end, location, description, repeat) VALUES (?,?,?,?,?,?)",
                (title, start, end, location, description, repeat)
            )
            return cur.lastrowid

    def get_events(self, start: float = None, end: float = None) -> list[dict]:
        now = time.time()
        s = start or now
        e = end or (now + 7 * 86400)
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM events WHERE start >= ? AND start <= ? ORDER BY start",
                (s, e)
            ).fetchall()
            return [dict(zip(["id","title","start","end","location","description","repeat","source"], r))
                    for r in rows]

    def delete_event(self, eid: int):
        with self._conn() as c:
            c.execute("DELETE FROM events WHERE id=?", (eid,))


# ─────────────────────────────────────────────
# Alarm / reminder daemon
# ─────────────────────────────────────────────

class AlarmDaemon:
    """
    Background thread that fires reminders and alarms at the right time.
    Calls on_reminder(reminder_dict) when a reminder is due.
    """

    def __init__(self, db: ProductivityDB, on_reminder: Callable, check_interval: float = 15):
        self.db = db
        self.on_reminder = on_reminder
        self.interval = check_interval
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._timers: dict[int, asyncio.TimerHandle] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def start(self, loop: asyncio.AbstractEventLoop = None):
        self._loop = loop or asyncio.get_event_loop()
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("Alarm daemon started.")

    def stop(self):
        self._running = False

    def _run(self):
        while self._running:
            upcoming = self.db.get_upcoming_reminders(within_seconds=self.interval + 5)
            now = time.time()
            for r in upcoming:
                rid = r["id"]
                if rid in self._timers:
                    continue
                delay = max(0, r["due"] - now)
                if self._loop and self._loop.is_running():
                    asyncio.run_coroutine_threadsafe(
                        self._fire(r, delay), self._loop
                    )
            time.sleep(self.interval)

    async def _fire(self, reminder: dict, delay: float):
        await asyncio.sleep(delay)
        self.db.mark_reminder_done(reminder["id"])
        if asyncio.iscoroutinefunction(self.on_reminder):
            await self.on_reminder(reminder)
        else:
            self.on_reminder(reminder)

        # Handle repeating reminders
        if reminder["repeat"] != "none":
            delta = {"daily": 86400, "weekly": 604800, "monthly": 2592000}.get(reminder["repeat"], 0)
            if delta:
                self.db.add_reminder(
                    reminder["title"],
                    reminder["due"] + delta,
                    reminder["repeat"],
                    reminder["notes"]
                )


# ─────────────────────────────────────────────
# Natural language time parser
# ─────────────────────────────────────────────

def parse_time(text: str) -> Optional[float]:
    """
    Parse natural language time expressions into Unix timestamps.
    Examples: "tomorrow 3pm", "in 2 hours", "friday at noon",
              "next monday 9am", "in 30 minutes"
    """
    now = datetime.now()
    text = text.lower().strip()

    # "in X minutes/hours/days"
    if m := re.search(r"in\s+(\d+)\s*(minute|hour|day|week)s?", text):
        n, unit = int(m.group(1)), m.group(2)
        delta = {"minute": 60, "hour": 3600, "day": 86400, "week": 604800}[unit]
        return (now + timedelta(seconds=n * delta)).timestamp()

    # "tomorrow [at] [time]"
    if "tomorrow" in text:
        base = now + timedelta(days=1)
        t = _extract_time_of_day(text) or (9, 0)
        return base.replace(hour=t[0], minute=t[1], second=0, microsecond=0).timestamp()

    # "today [at] [time]"
    if "today" in text:
        t = _extract_time_of_day(text) or (now.hour + 1, 0)
        return now.replace(hour=t[0], minute=t[1], second=0, microsecond=0).timestamp()

    # Day names: "monday", "next friday", etc.
    days = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]
    for i, day in enumerate(days):
        if day in text:
            days_ahead = (i - now.weekday()) % 7
            if days_ahead == 0 and "next" not in text:
                days_ahead = 7
            base = now + timedelta(days=days_ahead)
            t = _extract_time_of_day(text) or (9, 0)
            return base.replace(hour=t[0], minute=t[1], second=0, microsecond=0).timestamp()

    # Try direct datetime parse
    for fmt in ["%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M", "%m/%d/%Y %H:%M",
                "%Y-%m-%d", "%d/%m/%Y", "%H:%M"]:
        try:
            dt = datetime.strptime(text, fmt)
            if dt.year == 1900:
                dt = dt.replace(year=now.year, month=now.month, day=now.day)
            return dt.timestamp()
        except ValueError:
            pass

    return None


def _extract_time_of_day(text: str):
    """Extract hour/minute from text. Returns (hour, minute) or None."""
    # "3pm", "3:30pm", "15:00", "noon", "midnight"
    if "noon" in text:
        return (12, 0)
    if "midnight" in text:
        return (0, 0)
    if m := re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text):
        h, mn, period = int(m.group(1)), int(m.group(2) or 0), m.group(3)
        if period == "pm" and h != 12:
            h += 12
        elif period == "am" and h == 12:
            h = 0
        return (h, mn)
    return None


def format_time(ts: float) -> str:
    """Format a timestamp for display."""
    dt = datetime.fromtimestamp(ts)
    now = datetime.now()
    diff = dt - now

    if 0 < diff.total_seconds() < 86400:
        return dt.strftime("today at %I:%M %p")
    elif 86400 <= diff.total_seconds() < 172800:
        return dt.strftime("tomorrow at %I:%M %p")
    else:
        return dt.strftime("%A %b %d at %I:%M %p")


# ─────────────────────────────────────────────
# Productivity Manager
# ─────────────────────────────────────────────

class Productivity:
    """
    JARVIS Productivity Manager.
    Handles reminders, notes, todos, calendar, timers.
    """

    def __init__(self, on_reminder: Callable = None):
        self.db = ProductivityDB()
        self._on_reminder = on_reminder or self._default_reminder_alert
        self.daemon = AlarmDaemon(self.db, self._on_reminder)
        self._active_timers: dict[str, asyncio.Task] = {}

    def start(self, loop=None):
        """Start the background alarm daemon."""
        self.daemon.start(loop)

    # ── Reminders ──────────────────────────────────────────

    def add_reminder(self, title: str, when_text: str, repeat: str = "none") -> str:
        ts = parse_time(when_text)
        if not ts:
            return f"I couldn't understand the time '{when_text}'. Try 'tomorrow at 3pm' or 'in 2 hours'."
        rid = self.db.add_reminder(title, ts, repeat)
        return f"Reminder set: '{title}' {format_time(ts)}. (ID: {rid})"

    def list_reminders(self) -> str:
        reminders = self.db.get_reminders()
        if not reminders:
            return "No upcoming reminders."
        lines = ["Upcoming reminders:"]
        for r in reminders[:10]:
            lines.append(f"  [{r['id']}] {r['title']} — {format_time(r['due'])}")
        return "\n".join(lines)

    # ── Notes ──────────────────────────────────────────────

    def add_note(self, title: str, content: str = "", tags: list = None) -> str:
        nid = self.db.add_note(title, content, tags)
        return f"Note saved: '{title}' (ID: {nid})"

    def list_notes(self, tag: str = None) -> str:
        notes = self.db.get_notes(tag=tag)
        if not notes:
            return "No notes found."
        lines = ["Your notes:"]
        for n in notes[:10]:
            pin = "📌 " if n["pinned"] else ""
            tags = f" [{', '.join(n['tags'])}]" if n["tags"] else ""
            lines.append(f"  [{n['id']}] {pin}{n['title']}{tags}")
        return "\n".join(lines)

    def get_note(self, note_id: int) -> str:
        notes = self.db.get_notes(include_done=True)
        for n in notes:
            if n["id"] == note_id:
                return f"**{n['title']}**\n{n['content']}"
        return f"Note {note_id} not found."

    def search_notes(self, query: str) -> str:
        results = self.db.search_notes(query)
        if not results:
            return f"No notes found for '{query}'."
        lines = [f"Found {len(results)} note(s) for '{query}':"]
        for n in results[:5]:
            preview = n["content"][:80].replace("\n", " ")
            lines.append(f"  [{n['id']}] {n['title']}: {preview}...")
        return "\n".join(lines)

    # ── Todos ──────────────────────────────────────────────

    def add_todo(self, task: str, priority: str = "medium", due_text: str = None, project: str = "") -> str:
        due = parse_time(due_text) if due_text else None
        tid = self.db.add_todo(task, priority, due, project)
        due_str = f" (due {format_time(due)})" if due else ""
        return f"Todo added: '{task}'{due_str} [{priority} priority] (ID: {tid})"

    def list_todos(self, project: str = None) -> str:
        todos = self.db.get_todos(project=project)
        if not todos:
            return "No pending todos."
        lines = ["Your todos:"]
        icons = {"high": "🔴", "medium": "🟡", "low": "🟢"}
        for t in todos:
            icon = icons.get(t["priority"], "•")
            due = f" · due {format_time(t['due'])}" if t.get("due") else ""
            proj = f" [{t['project']}]" if t.get("project") else ""
            lines.append(f"  {icon} [{t['id']}] {t['task']}{due}{proj}")
        return "\n".join(lines)

    def complete_todo(self, tid: int) -> str:
        self.db.complete_todo(tid)
        return f"Todo {tid} marked as complete. ✓"

    # ── Calendar ───────────────────────────────────────────

    def add_event(self, title: str, when_text: str, duration_mins: int = 60,
                  location: str = "", description: str = "") -> str:
        start = parse_time(when_text)
        if not start:
            return f"Couldn't parse time: '{when_text}'"
        end = start + duration_mins * 60
        eid = self.db.add_event(title, start, end, location, description)
        return f"Event added: '{title}' on {format_time(start)} (ID: {eid})"

    def get_schedule(self, days: int = 7) -> str:
        end = time.time() + days * 86400
        events = self.db.get_events(end=end)
        if not events:
            return f"No events in the next {days} days."
        lines = [f"Schedule for the next {days} days:"]
        for e in events:
            dt = datetime.fromtimestamp(e["start"]).strftime("%a %b %d · %I:%M %p")
            loc = f" @ {e['location']}" if e.get("location") else ""
            lines.append(f"  📅 {dt} — {e['title']}{loc}")
        return "\n".join(lines)

    def get_today(self) -> str:
        now = time.time()
        midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        end_of_day = midnight + 86400
        events = self.db.get_events(start=midnight, end=end_of_day)
        reminders = [r for r in self.db.get_reminders() if midnight <= r["due"] <= end_of_day]
        todos = [t for t in self.db.get_todos() if t.get("due") and midnight <= t["due"] <= end_of_day]

        lines = [f"Today — {datetime.now().strftime('%A, %B %d')}"]
        if events:
            lines.append("\nEvents:")
            for e in events:
                lines.append(f"  📅 {datetime.fromtimestamp(e['start']).strftime('%I:%M %p')} — {e['title']}")
        if reminders:
            lines.append("\nReminders:")
            for r in reminders:
                lines.append(f"  🔔 {datetime.fromtimestamp(r['due']).strftime('%I:%M %p')} — {r['title']}")
        if todos:
            lines.append("\nTodos due today:")
            for t in todos:
                lines.append(f"  ✓ {t['task']}")
        if not events and not reminders and not todos:
            lines.append("\nNothing scheduled for today.")
        return "\n".join(lines)

    # ── Timer ──────────────────────────────────────────────

    async def start_timer(self, duration_text: str, label: str = "Timer") -> str:
        ts = parse_time(f"in {duration_text}") if not duration_text.startswith("in") else parse_time(duration_text)
        if not ts:
            return f"Couldn't parse duration: '{duration_text}'"
        seconds = ts - time.time()
        if seconds <= 0:
            return "Timer duration must be in the future."

        async def _timer():
            await asyncio.sleep(seconds)
            logger.info(f"⏰ Timer done: {label}")
            await self._on_reminder({"title": f"⏰ {label}", "due": time.time(), "id": -1})

        task = asyncio.create_task(_timer())
        self._active_timers[label] = task
        return f"Timer started: '{label}' — {int(seconds // 60)}m {int(seconds % 60)}s"

    def stop_timer(self, label: str = None) -> str:
        if label and label in self._active_timers:
            self._active_timers[label].cancel()
            del self._active_timers[label]
            return f"Timer '{label}' cancelled."
        elif self._active_timers:
            last = list(self._active_timers.keys())[-1]
            self._active_timers[last].cancel()
            del self._active_timers[last]
            return f"Timer '{last}' cancelled."
        return "No active timers."

    # ── Natural language handler ────────────────────────────

    async def handle(self, command: str) -> Optional[str]:
        cmd = command.lower().strip()

        # Reminders
        if m := re.search(r"remind me (?:to\s+)?(.+?) (?:at|on|in|tomorrow|today|next|every)\s+(.+)", cmd):
            return self.add_reminder(m.group(1).strip(), m.group(2).strip())
        if re.search(r"(?:list|show|what are)\s+(?:my\s+)?reminders", cmd):
            return self.list_reminders()

        # Notes
        if m := re.search(r"(?:take a note|note down|write down|save a note)[:\s]+(.+)", cmd):
            parts = m.group(1).split(":", 1)
            title, content = (parts[0].strip(), parts[1].strip()) if len(parts) > 1 else (parts[0].strip(), "")
            return self.add_note(title, content)
        if re.search(r"(?:list|show|my)\s+notes", cmd):
            return self.list_notes()
        if m := re.search(r"search (?:my )?notes (?:for\s+)?(.+)", cmd):
            return self.search_notes(m.group(1).strip())

        # Todos
        if m := re.search(r"(?:add|create)\s+(?:a\s+)?todo[:\s]+(.+)", cmd):
            task = m.group(1).strip()
            priority = "high" if "urgent" in cmd or "important" in cmd else "medium"
            return self.add_todo(task, priority)
        if re.search(r"(?:list|show|my)\s+todos?", cmd):
            return self.list_todos()
        if m := re.search(r"(?:complete|done|finish|mark done)\s+todo\s+#?(\d+)", cmd):
            return self.complete_todo(int(m.group(1)))

        # Calendar
        if m := re.search(r"(?:schedule|add|create)\s+(?:an?\s+)?(?:event|meeting|appointment)[:\s]+(.+?)(?:\s+(?:at|on)\s+(.+))?$", cmd):
            title = m.group(1).strip()
            when = m.group(2) or "tomorrow 9am"
            return self.add_event(title, when)
        if re.search(r"(?:what's|show|my)\s+(?:today|schedule|calendar|agenda)", cmd):
            return self.get_today()
        if m := re.search(r"(?:next|upcoming|this)\s+week", cmd):
            return self.get_schedule(7)

        # Timer
        if m := re.search(r"(?:set|start)\s+(?:a\s+)?timer\s+(?:for\s+)?(.+?)(?:\s+(?:called|named|for)\s+(.+))?$", cmd):
            duration = m.group(1).strip()
            label = m.group(2).strip() if m.group(2) else "Timer"
            return await self.start_timer(duration, label)
        if re.search(r"(?:stop|cancel)\s+(?:the\s+)?timer", cmd):
            return self.stop_timer()

        return None

    async def _default_reminder_alert(self, reminder: dict):
        logger.info(f"🔔 REMINDER: {reminder['title']}")
        # This will be overridden by the voice TTS when integrated
