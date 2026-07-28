from __future__ import annotations

import sqlite3

from app.database import Database


def test_initialize_adds_public_reply_columns_to_existing_database(settings):
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(settings.db_path) as conn:
        conn.execute(
            """
            CREATE TABLE processed_comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                comment_id TEXT NOT NULL UNIQUE,
                product_id INTEGER NOT NULL,
                commenter_id TEXT,
                commenter_username TEXT,
                comment_text TEXT NOT NULL,
                dm_message TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                error_message TEXT,
                created_at TEXT NOT NULL,
                processed_at TEXT
            )
            """
        )

    database = Database(settings.db_path)
    database.initialize()

    with database.connect() as conn:
        columns = {
            row["name"]: row for row in conn.execute("PRAGMA table_info(processed_comments)")
        }

    assert "reply_message" in columns
    assert "reply_status" in columns
    assert "reply_error_message" in columns
    assert "replied_at" in columns
    assert columns["reply_status"]["dflt_value"] == "'pending'"
