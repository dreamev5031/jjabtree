from __future__ import annotations

import sqlite3

from app.database import Database


def test_initialize_adds_media_check_columns_to_existing_products_table(settings):
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(settings.db_path) as conn:
        conn.execute(
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
            )
            """
        )
        conn.execute(
            """
            INSERT INTO products (
                product_name, purchase_link, trigger_phrase, photo_url,
                ig_media_id, ig_permalink, created_at, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active')
            """,
            (
                "기존 상품",
                "https://example.com/product",
                "링크",
                "/uploads/test.jpg",
                "legacy-media",
                "https://instagram.com/reel/test",
                "2026-07-28T00:00:00+00:00",
            ),
        )

    database = Database(settings.db_path)
    database.initialize()

    with database.connect() as conn:
        columns = {
            row["name"]: row for row in conn.execute("PRAGMA table_info(products)")
        }
        row = conn.execute("SELECT * FROM products WHERE id = 1").fetchone()

    assert "media_check_status" in columns
    assert "media_checked_at" in columns
    assert columns["media_check_status"]["dflt_value"] == "'unchecked'"
    assert row["media_check_status"] == "unchecked"
    assert row["media_checked_at"] is None