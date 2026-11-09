# -*- coding: utf-8 -*-
"""
بکاپ ابری دیتابیس ربات — از خود تلگرام به‌عنوان فضای ذخیره‌سازی!

سرورهای رایگان معمولاً بعد از هر ری‌استارت فایل‌ها رو پاک می‌کنن.
این ماژول دیتابیس ضد تکرار (reposter.db) رو:
  • موقع روشن شدن: آخرین بکاپ رو از «پیام‌های ذخیره‌شده» برمی‌گردونه
  • هر چند ساعت: یه نسخه جدید در «پیام‌های ذخیره‌شده» می‌فرسته
  • موقع خاموش شدن: یه بکاپ نهایی می‌گیره
و نسخه‌های خیلی قدیمی رو هم خودش پاک می‌کنه (آخرین ۳ نسخه می‌مونه)
"""

import asyncio
import logging
import os

import config

log = logging.getLogger("backup")

BACKUP_TAG = "#reposter_backup"
KEEP_VERSIONS = 3


async def backup_now(client):
    """ارسال نسخه جدید دیتابیس به پیام‌های ذخیره‌شده + پاکسازی نسخه‌های قدیمی"""
    if not os.path.exists(config.DB_FILE):
        return None
    sent = await client.send_file(
        "me", config.DB_FILE,
        caption=BACKUP_TAG, force_document=True,
    )
    # حذف نسخه‌های قدیمی‌تر از حد مجاز
    try:
        ids = []
        async for m in client.iter_messages("me", search=BACKUP_TAG, limit=15):
            if m.document is not None:
                ids.append(m.id)
        for mid in ids[KEEP_VERSIONS:]:
            await client.delete_messages("me", [mid])
    except Exception:
        log.exception("Cleaning old backups failed (not critical)")
    log.info("Database backup saved to Saved Messages (message %s)",
             getattr(sent, "id", "?"))
    return sent


async def restore_backup(client):
    """برگرداندن آخرین بکاپ دیتابیس — فقط اگر از فایل محلی جدیدتر باشد"""
    try:
        async for m in client.iter_messages("me", search=BACKUP_TAG, limit=5):
            if m.document is None:
                continue
            fname = ""
            for attr in m.document.attributes:
                name = getattr(attr, "file_name", None)
                if name:
                    fname = name
                    break
            if "reposter" not in (fname or "").lower():
                continue

            mtime = os.path.getmtime(config.DB_FILE) if os.path.exists(config.DB_FILE) else 0
            if m.date is not None and m.date.timestamp() <= mtime:
                log.info("Telegram backup is not newer than local file; not restored")
                return False

            tmp = config.DB_FILE + ".restore"
            path = await client.download_media(m, file=tmp)
            if path and os.path.exists(path):
                os.replace(path, config.DB_FILE)
                log.info("Dedup database restored from Telegram backup")
                return True
    except Exception:
        log.exception("Backup restore failed (normal on first run)")
    return False


async def periodic_backup(client):
    """حلقه بکاپ دوره‌ای"""
    interval = max(1, config.BACKUP_INTERVAL_HOURS) * 3600
    while True:
        await asyncio.sleep(interval)
        try:
            await backup_now(client)
        except Exception:
            log.exception("Periodic backup failed")
