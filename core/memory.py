"""
JARVIS Memory — core/memory.py
Persistent, semantic memory using ChromaDB (vector search) with SQLite fallback.
Remembers conversations, facts, and user preferences across sessions.
"""

import os
import time
import json
import sqlite3
import hashlib
import logging
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, asdict

logger = logging.getLogger("jarvis.memory")


@dataclass
class MemoryEntry:
    id: str
    content: str
    role: str           # "user" | "assistant" | "fact" | "preference"
    timestamp: float
    session_id: str
    metadata: dict

    def to_dict(self) -> dict:
        return asdict(self)


class ChromaMemory:
    """Vector-based semantic memory using ChromaDB."""

    def __init__(self, persist_path: str, embedding_model: str = "all-MiniLM-L6-v2"):
        self.path = Path(persist_path).expanduser()
        self.path.mkdir(parents=True, exist_ok=True)
        self.embedding_model = embedding_model
        self._client = None
        self._collection = None
        self._init()

    def _init(self):
        try:
            import chromadb
            from chromadb.utils import embedding_functions
            self._client = chromadb.PersistentClient(path=str(self.path))
            ef = embedding_functions.SentenceTransformerEmbeddingFunction(
                model_name=self.embedding_model
            )
            self._collection = self._client.get_or_create_collection(
                name="jarvis_memory",
                embedding_function=ef,
                metadata={"hnsw:space": "cosine"}
            )
            logger.info(f"ChromaDB memory initialized at {self.path}")
        except ImportError:
            raise RuntimeError("chromadb not installed. Run: pip install chromadb sentence-transformers")

    def add(self, entry: MemoryEntry):
        self._collection.add(
            ids=[entry.id],
            documents=[entry.content],
            metadatas=[{
                "role": entry.role,
                "timestamp": entry.timestamp,
                "session_id": entry.session_id,
                **{k: str(v) for k, v in entry.metadata.items()}
            }]
        )

    def search(self, query: str, n: int = 5, role_filter: str = None) -> list[MemoryEntry]:
        where = {"role": role_filter} if role_filter else None
        results = self._collection.query(
            query_texts=[query],
            n_results=min(n, self._collection.count() or 1),
            where=where
        )
        entries = []
        for i, doc in enumerate(results["documents"][0]):
            meta = results["metadatas"][0][i]
            entries.append(MemoryEntry(
                id=results["ids"][0][i],
                content=doc,
                role=meta.get("role", "unknown"),
                timestamp=float(meta.get("timestamp", 0)),
                session_id=meta.get("session_id", ""),
                metadata={k: v for k, v in meta.items() if k not in ("role", "timestamp", "session_id")}
            ))
        return entries

    def get_recent(self, n: int = 20, session_id: str = None) -> list[MemoryEntry]:
        where = {"session_id": session_id} if session_id else None
        results = self._collection.get(where=where, limit=n)
        entries = []
        for i, doc in enumerate(results["documents"]):
            meta = results["metadatas"][i]
            entries.append(MemoryEntry(
                id=results["ids"][i],
                content=doc,
                role=meta.get("role", "unknown"),
                timestamp=float(meta.get("timestamp", 0)),
                session_id=meta.get("session_id", ""),
                metadata={}
            ))
        return sorted(entries, key=lambda e: e.timestamp)

    def count(self) -> int:
        return self._collection.count()

    def clear_session(self, session_id: str):
        results = self._collection.get(where={"session_id": session_id})
        if results["ids"]:
            self._collection.delete(ids=results["ids"])


class SQLiteMemory:
    """Fallback memory using SQLite — no embeddings, recency-based."""

    def __init__(self, persist_path: str):
        self.path = Path(persist_path).expanduser()
        self.path.mkdir(parents=True, exist_ok=True)
        self.db_path = self.path / "memory.db"
        self._init_db()
        logger.info(f"SQLite memory initialized at {self.db_path}")

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    role TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    session_id TEXT,
                    metadata TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_session ON memories(session_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON memories(timestamp)")

    def add(self, entry: MemoryEntry):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO memories VALUES (?, ?, ?, ?, ?, ?)",
                (entry.id, entry.content, entry.role, entry.timestamp,
                 entry.session_id, json.dumps(entry.metadata))
            )

    def search(self, query: str, n: int = 5, role_filter: str = None) -> list[MemoryEntry]:
        # Basic keyword search (no semantic embedding)
        words = query.lower().split()
        like_clauses = " OR ".join(["LOWER(content) LIKE ?" for _ in words])
        params = [f"%{w}%" for w in words]
        sql = f"SELECT * FROM memories WHERE ({like_clauses})"
        if role_filter:
            sql += " AND role = ?"
            params.append(role_filter)
        sql += f" ORDER BY timestamp DESC LIMIT {n}"
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def get_recent(self, n: int = 20, session_id: str = None) -> list[MemoryEntry]:
        sql = "SELECT * FROM memories"
        params = []
        if session_id:
            sql += " WHERE session_id = ?"
            params.append(session_id)
        sql += f" ORDER BY timestamp DESC LIMIT {n}"
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(sql, params).fetchall()
        return list(reversed([self._row_to_entry(r) for r in rows]))

    def count(self) -> int:
        with sqlite3.connect(self.db_path) as conn:
            return conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]

    def clear_session(self, session_id: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM memories WHERE session_id = ?", (session_id,))

    def _row_to_entry(self, row) -> MemoryEntry:
        return MemoryEntry(
            id=row[0], content=row[1], role=row[2],
            timestamp=row[3], session_id=row[4],
            metadata=json.loads(row[5]) if row[5] else {}
        )


class Memory:
    """
    JARVIS Memory Manager.
    Uses ChromaDB for semantic search with automatic SQLite fallback.
    """

    def __init__(self, config: dict = None):
        cfg = config or {}
        persist_path = cfg.get("persist_path", "~/.jarvis/memory")
        self.embedding_model = cfg.get("embedding_model", "all-MiniLM-L6-v2")
        self.max_context = cfg.get("max_context_messages", 20)
        self.backend_name = cfg.get("backend", "chroma")
        self._backend = self._init_backend(persist_path)
        self.current_session = self._new_session_id()

    def _init_backend(self, path: str):
        if self.backend_name == "chroma":
            try:
                return ChromaMemory(path, self.embedding_model)
            except Exception as e:
                logger.warning(f"ChromaDB unavailable ({e}), falling back to SQLite.")
        return SQLiteMemory(path)

    def _new_session_id(self) -> str:
        return hashlib.md5(str(time.time()).encode()).hexdigest()[:8]

    def _make_id(self, content: str, role: str) -> str:
        return hashlib.md5(f"{content}{role}{time.time()}".encode()).hexdigest()

    # ── Public API ─────────────────────────────

    def remember(self, content: str, role: str = "user", metadata: dict = None):
        """Store a memory entry."""
        entry = MemoryEntry(
            id=self._make_id(content, role),
            content=content,
            role=role,
            timestamp=time.time(),
            session_id=self.current_session,
            metadata=metadata or {}
        )
        self._backend.add(entry)
        logger.debug(f"Remembered [{role}]: {content[:60]}...")

    def recall(self, query: str, n: int = 5, role_filter: str = None) -> list[MemoryEntry]:
        """Semantic search through memories."""
        return self._backend.search(query, n=n, role_filter=role_filter)

    def get_context(self, n: int = None) -> list[dict]:
        """Get recent messages formatted for LLM context."""
        limit = n or self.max_context
        recent = self._backend.get_recent(n=limit, session_id=self.current_session)
        context = []
        for entry in recent:
            if entry.role in ("user", "assistant"):
                context.append({"role": entry.role, "content": entry.content})
        return context

    def remember_fact(self, fact: str):
        """Store a long-term fact about the user."""
        self.remember(fact, role="fact")

    def remember_preference(self, preference: str):
        """Store a user preference."""
        self.remember(preference, role="preference")

    def get_user_facts(self, query: str = "") -> list[str]:
        """Retrieve facts about the user."""
        if query:
            entries = self._backend.search(query, n=5, role_filter="fact")
        else:
            entries = self._backend.get_recent(n=20)
            entries = [e for e in entries if e.role == "fact"]
        return [e.content for e in entries]

    def new_session(self):
        """Start a fresh conversation session."""
        self.current_session = self._new_session_id()
        logger.info(f"New session: {self.current_session}")

    def stats(self) -> dict:
        return {
            "backend": type(self._backend).__name__,
            "total_memories": self._backend.count(),
            "current_session": self.current_session,
        }


# ─────────────────────────────────────────────
# Quick test
# ─────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    memory = Memory({"persist_path": "/tmp/jarvis_test_memory", "backend": "sqlite"})

    memory.remember("The user's name is Alex.", role="fact")
    memory.remember("How are you today?", role="user")
    memory.remember("I'm functioning at optimal capacity, Alex.", role="assistant")
    memory.remember("Open Spotify for me.", role="user")
    memory.remember("Opening Spotify now.", role="assistant")
    memory.remember_preference("User prefers concise responses.")

    print("\n📚 Memory Stats:", memory.stats())
    print("\n🔍 Recent context:")
    for msg in memory.get_context():
        print(f"  [{msg['role']}]: {msg['content']}")

    print("\n🧠 Search 'Spotify':")
    for entry in memory.recall("Spotify"):
        print(f"  [{entry.role}] {entry.content}")
