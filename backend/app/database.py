from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_name TEXT NOT NULL,
    purchase_link TEXT NOT NULL,
    trigger_phrase TEXT NOT NULL,
    photo_url TEXT NOT NULL,
    ig_media_id TEXT NOT NULL UNIQUE,
    ig_permalink TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive'))
);

CREATE INDEX IF NOT EXISTS idx_products_status_id ON products(status, id);
CREATE INDEX IF NOT EXISTS idx_products_media_id ON products(ig_media_id);

CREATE TABLE IF NOT EXISTS processed_comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    comment_id TEXT NOT NULL UNIQUE,
    product_id INTEGER NOT NULL,
    commenter_id TEXT,
    commenter_username TEXT,
    comment_text TEXT NOT NULL,
    dm_message TEXT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'sent', 'failed', 'ignored')),
    error_message TEXT,
    reply_message TEXT,
    reply_status TEXT NOT NULL DEFAULT 'pending' CHECK (reply_status IN ('pending', 'sent', 'failed', 'skipped')),
    reply_error_message TEXT,
    created_at TEXT NOT NULL,
    processed_at TEXT,
    replied_at TEXT,
    FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_processed_comments_product_id
ON processed_comments(product_id, created_at DESC);
"""


# Existing Railway SQLite volumes may have been created before public replies existed.
# SQLite's CREATE TABLE IF NOT EXISTS does not add new columns, so initialize() applies
# these safe additive migrations when required.
PROCESSED_COMMENT_MIGRATIONS = {
    "reply_message": "TEXT",
    "reply_status": "TEXT NOT NULL DEFAULT 'pending'",
    "reply_error_message": "TEXT",
    "replied_at": "TEXT",
}


class Database:
    def __init__(self, path: Path):
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            self._migrate_processed_comments(conn)

    @staticmethod
    def _migrate_processed_comments(conn: sqlite3.Connection) -> None:
        existing_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(processed_comments)").fetchall()
        }
        for column_name, column_definition in PROCESSED_COMMENT_MIGRATIONS.items():
            if column_name not in existing_columns:
                conn.execute(
                    f"ALTER TABLE processed_comments ADD COLUMN {column_name} {column_definition}"
                )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=15, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def list_public_products(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, product_name, purchase_link, photo_url, ig_permalink, created_at
                FROM products
                WHERE status = 'active'
                ORDER BY id ASC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def list_admin_products(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM products ORDER BY id DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def create_product(self, values: dict[str, Any]) -> dict[str, Any]:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO products (
                    product_name, purchase_link, trigger_phrase, photo_url,
                    ig_media_id, ig_permalink, created_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active')
                """,
                (
                    values["product_name"],
                    values["purchase_link"],
                    values["trigger_phrase"],
                    values["photo_url"],
                    values["ig_media_id"],
                    values["ig_permalink"],
                    self.now(),
                ),
            )
            row = conn.execute(
                "SELECT * FROM products WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
        return dict(row)

    def update_product_status(self, product_id: int, status: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE products SET status = ? WHERE id = ?", (status, product_id)
            )
            row = conn.execute(
                "SELECT * FROM products WHERE id = ?", (product_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_active_product_by_media(self, media_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM products
                WHERE ig_media_id = ? AND status = 'active'
                LIMIT 1
                """,
                (media_id,),
            ).fetchone()
        return dict(row) if row else None

    def claim_comment(
        self,
        *,
        comment_id: str,
        product_id: int,
        commenter_id: str | None,
        commenter_username: str | None,
        comment_text: str,
    ) -> bool:
        try:
            with self.connect() as conn:
                conn.execute(
                    """
                    INSERT INTO processed_comments (
                        comment_id, product_id, commenter_id, commenter_username,
                        comment_text, status, reply_status, created_at
                    ) VALUES (?, ?, ?, ?, ?, 'pending', 'pending', ?)
                    """,
                    (
                        comment_id,
                        product_id,
                        commenter_id,
                        commenter_username,
                        comment_text,
                        self.now(),
                    ),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def complete_comment(
        self,
        comment_id: str,
        *,
        status: str,
        dm_message: str | None = None,
        error_message: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE processed_comments
                SET status = ?, dm_message = ?, error_message = ?, processed_at = ?
                WHERE comment_id = ?
                """,
                (status, dm_message, error_message, self.now(), comment_id),
            )

    def complete_public_reply(
        self,
        comment_id: str,
        *,
        status: str,
        reply_message: str | None = None,
        error_message: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE processed_comments
                SET reply_status = ?, reply_message = ?, reply_error_message = ?, replied_at = ?
                WHERE comment_id = ?
                """,
                (status, reply_message, error_message, self.now(), comment_id),
            )

    def list_dm_logs(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT pc.*, p.product_name
                FROM processed_comments pc
                JOIN products p ON p.id = pc.product_id
                ORDER BY pc.id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]
