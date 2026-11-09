# -*- coding: utf-8 -*-
"""
دیتابیس SQLite ربات بازنشر:
- جدول posted: تاریخچه پست‌های ارسال‌شده (ضد تکرار دائمی)
- جدول sources: لیست کانال‌های منبع
- جدول settings: تنظیمات داخل تلگرامی (مثل کانال مقصد)
"""

import sqlite3
import threading

import config

_conn = None
_lock = threading.Lock()


def init():
    global _conn
    _conn = sqlite3.connect(config.DB_FILE, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS posted (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            text_hash      TEXT,
            media_uid      TEXT,
            source_chat_id INTEGER,
            source_msg_id  INTEGER,
            dest_msg_id    INTEGER,
            source_title   TEXT,
            created_at     TEXT DEFAULT (datetime('now', 'localtime'))
        );
        CREATE INDEX IF NOT EXISTS idx_posted_text ON posted(text_hash);
        CREATE INDEX IF NOT EXISTS idx_posted_media ON posted(media_uid);

        CREATE TABLE IF NOT EXISTS sources (
            chat_id    INTEGER PRIMARY KEY,
            identifier TEXT,
            username   TEXT,
            title      TEXT,
            added_at   TEXT DEFAULT (datetime('now', 'localtime'))
        );

        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
        """
    )
    _conn.commit()


def _exec(sql, params=(), fetch=None):
    with _lock:
        cur = _conn.execute(sql, params)
        if fetch == "one":
            row = cur.fetchone()
            _conn.commit()
            return row
        if fetch == "all":
            return cur.fetchall()
        _conn.commit()
        return cur.lastrowid


# ---------------- ضد تکرار ----------------

def has_text(text_hash):
    if not text_hash:
        return False
    return _exec(
        "SELECT 1 FROM posted WHERE text_hash = ? LIMIT 1",
        (text_hash,), fetch="one",
    ) is not None


def has_media(media_uid):
    if not media_uid:
        return False
    return _exec(
        "SELECT 1 FROM posted WHERE media_uid = ? LIMIT 1",
        (media_uid,), fetch="one",
    ) is not None


def mark_posted(text_hash, media_uid, source_chat_id, source_msg_id,
                dest_msg_id, source_title):
    _exec(
        """INSERT INTO posted
           (text_hash, media_uid, source_chat_id, source_msg_id, dest_msg_id, source_title)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (text_hash, media_uid, source_chat_id, source_msg_id, dest_msg_id, source_title),
    )


def count_posted():
    row = _exec("SELECT COUNT(*) AS c FROM posted", fetch="one")
    return row["c"] if row else 0


def clear_history():
    _exec("DELETE FROM posted")


# ---------------- کانال‌های منبع ----------------

def add_source(chat_id, identifier, username, title):
    _exec(
        """INSERT OR REPLACE INTO sources (chat_id, identifier, username, title)
           VALUES (?, ?, ?, ?)""",
        (chat_id, identifier, username, title),
    )


def remove_source(chat_id):
    _exec("DELETE FROM sources WHERE chat_id = ?", (chat_id,))


def get_sources():
    return _exec("SELECT * FROM sources ORDER BY title", fetch="all")


def find_source_by_identifier(identifier):
    ident = (identifier or "").lower().lstrip("@")
    return _exec(
        "SELECT * FROM sources WHERE LOWER(identifier) = ? OR LOWER(identifier) = ?",
        (identifier.lower(), "@" + ident), fetch="one",
    )


# ---------------- تنظیمات ----------------

def get_setting(key, default=None):
    row = _exec("SELECT value FROM settings WHERE key = ?", (key,), fetch="one")
    return row["value"] if row else default


def set_setting(key, value):
    _exec(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        (key, value),
    )
