"""SQLite storage: watchlists per chat, and which items we've already sent."""
import hashlib
import sqlite3
import threading
import time

_lock = threading.Lock()


class Store:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS chats (
                chat_id INTEGER PRIMARY KEY,
                paused  INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS watch (
                chat_id INTEGER NOT NULL,
                name    TEXT    NOT NULL,
                kind    TEXT    NOT NULL,          -- 'stock' or 'topic'
                PRIMARY KEY (chat_id, name)
            );
            CREATE TABLE IF NOT EXISTS seen (
                chat_id INTEGER NOT NULL,
                key     TEXT    NOT NULL,          -- hash of url/title
                ts      INTEGER NOT NULL,
                PRIMARY KEY (chat_id, key)
            );
            """
        )
        self.db.commit()

    # --- chats -------------------------------------------------------------
    def ensure_chat(self, chat_id: int):
        with _lock:
            self.db.execute("INSERT OR IGNORE INTO chats(chat_id) VALUES (?)", (chat_id,))
            self.db.commit()

    def set_paused(self, chat_id: int, paused: bool):
        self.ensure_chat(chat_id)
        with _lock:
            self.db.execute("UPDATE chats SET paused=? WHERE chat_id=?", (int(paused), chat_id))
            self.db.commit()

    def active_chats(self) -> list[int]:
        rows = self.db.execute("SELECT chat_id FROM chats WHERE paused=0").fetchall()
        return [r[0] for r in rows]

    # --- watchlist ---------------------------------------------------------
    def add(self, chat_id: int, name: str, kind: str) -> bool:
        self.ensure_chat(chat_id)
        with _lock:
            cur = self.db.execute(
                "INSERT OR IGNORE INTO watch(chat_id, name, kind) VALUES (?,?,?)",
                (chat_id, name, kind),
            )
            self.db.commit()
            return cur.rowcount > 0

    def remove(self, chat_id: int, name: str) -> bool:
        with _lock:
            cur = self.db.execute(
                "DELETE FROM watch WHERE chat_id=? AND lower(name)=lower(?)", (chat_id, name)
            )
            self.db.commit()
            return cur.rowcount > 0

    def items(self, chat_id: int) -> list[tuple[str, str]]:
        return self.db.execute(
            "SELECT name, kind FROM watch WHERE chat_id=? ORDER BY name", (chat_id,)
        ).fetchall()

    # --- dedup -------------------------------------------------------------
    @staticmethod
    def _key(item: dict) -> str:
        raw = (item.get("url") or "") + "|" + (item.get("title") or "").strip().lower()
        return hashlib.sha1(raw.encode()).hexdigest()

    def filter_unseen(self, chat_id: int, items: list[dict]) -> list[dict]:
        out = []
        for it in items:
            row = self.db.execute(
                "SELECT 1 FROM seen WHERE chat_id=? AND key=?", (chat_id, self._key(it))
            ).fetchone()
            if not row:
                out.append(it)
        return out

    def mark_seen(self, chat_id: int, items: list[dict]):
        now = int(time.time())
        with _lock:
            self.db.executemany(
                "INSERT OR IGNORE INTO seen(chat_id, key, ts) VALUES (?,?,?)",
                [(chat_id, self._key(it), now) for it in items],
            )
            # forget anything older than 14 days so the table stays small
            self.db.execute("DELETE FROM seen WHERE ts < ?", (now - 14 * 86400,))
            self.db.commit()
