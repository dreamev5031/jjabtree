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

    assert "product_name_snapshot" in columns
    assert "reply_message" in columns
    assert "reply_status" in columns
    assert "reply_error_message" in columns
    assert "replied_at" in columns
    assert columns["reply_status"]["dflt_value"] == "'pending'"


def test_initialize_removes_cascade_delete_and_preserves_existing_history(settings):
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(settings.db_path) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(
            """
            CREATE TABLE products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_name TEXT NOT NULL,
                purchase_link TEXT NOT NULL,
                trigger_phrase TEXT NOT NULL,
                photo_url TEXT NOT NULL,
                ig_media_id TEXT NOT NULL UNIQUE,
                ig_permalink TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active'
            );

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
                reply_message TEXT,
                reply_status TEXT NOT NULL DEFAULT 'pending',
                reply_error_message TEXT,
                created_at TEXT NOT NULL,
                processed_at TEXT,
                replied_at TEXT,
                FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
            );

            INSERT INTO products (
                product_name, purchase_link, trigger_phrase, photo_url,
                ig_media_id, ig_permalink, created_at, status
            ) VALUES (
                '기존 상품', 'https://example.com', '링크', '/uploads/old.jpg',
                'media-old', 'https://instagram.com/reel/old', '2026-01-01T00:00:00Z', 'active'
            );

            INSERT INTO processed_comments (
                comment_id, product_id, commenter_id, commenter_username,
                comment_text, dm_message, status, reply_status, created_at
            ) VALUES (
                'comment-old', 1, 'user-old', 'old-user',
                '링크', '001번 확인', 'sent', 'sent', '2026-01-01T00:00:00Z'
            );
            """
        )

    database = Database(settings.db_path)
    database.initialize()

    with database.connect() as conn:
        assert conn.execute("PRAGMA foreign_key_list(processed_comments)").fetchall() == []

    deleted = database.delete_product(1)
    assert deleted is not None

    logs = database.list_dm_logs()
    assert len(logs) == 1
    assert logs[0]["product_id"] == 1
    assert logs[0]["product_name"] == "기존 상품"
    assert logs[0]["comment_id"] == "comment-old"

    next_product = database.create_product(
        {
            "product_name": "새 상품",
            "purchase_link": "https://example.com/new",
            "trigger_phrase": "링크",
            "photo_url": "/uploads/new.jpg",
            "ig_media_id": "media-new",
            "ig_permalink": "https://instagram.com/reel/new",
        }
    )
    assert next_product["id"] == 2
