# -*- coding: utf-8 -*-
"""
منطق اصلی ربات بازنشر تلگرام:
- گوش دادن به پست‌های جدید کانال‌های منبع
- کپی کامل (بدون نوار فوروارد) + دکمه لینک منبع
- جلوگیری از پست تکراری با دیتابیس دائمی
- صف ارسال با فاصله زمانی + محدودیت سرعت
- مدیریت کامل با دستورات تلگرام در «پیام‌های ذخیره‌شده»
"""

import asyncio
import hashlib
import logging
import os
import re
import shutil
import tempfile
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from telethon import Button, events
from telethon import utils as tl_utils
from telethon.errors import FloodWaitError
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl import types as tlt

import config
import database as db
from cleaner import clean_message_text, fit_text

log = logging.getLogger("reposter")

MAX_TEXT = 4096      # سقف متن پست متنی
MAX_CAPTION = 1024   # سقف کپشن مدیا و آلبوم

# انواع مدیایی که قابل کپی مستقیم نیستن (پست متنی حساب میشن)
_SKIP_MEDIA_CLASSES = tuple(
    cls for cls in (
        getattr(tlt, "MessageMediaWebPage", None),
        getattr(tlt, "MessageMediaPoll", None),
        getattr(tlt, "MessageMediaContact", None),
        getattr(tlt, "MessageMediaDice", None),
        getattr(tlt, "MessageMediaGame", None),
        getattr(tlt, "MessageMediaGiveaway", None),
        getattr(tlt, "MessageMediaGiveawayWinners", None),
    ) if cls is not None
)


def _real_media(msg):
    """مدیای واقعی قابل کپی؛ پیش‌نمایش وب/نظرسنجی/... به‌عنوان بدون‌مدیا حساب میشه"""
    m = msg.media
    if m is None:
        return None
    if _SKIP_MEDIA_CLASSES and isinstance(m, _SKIP_MEDIA_CLASSES):
        return None
    return m


def _media_uid(msg):
    """شناسه یکتای فایل مدیا برای تشخیص تکرار (مستقل از کانال و پیام)"""
    try:
        if msg.photo is not None:
            return "ph:" + str(msg.photo.file.unique_id)
        if msg.document is not None:
            return "doc:" + str(msg.document.file.unique_id)
    except Exception:
        pass
    return None


def _text_hash(text):
    """هش متنِ نرمال‌شده برای تشخیص تکرار"""
    if not text or not text.strip():
        return None
    norm = " ".join(text.split())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def _source_link(msg, username):
    """ساخت لینک مستقیم به پست اصلی کانال منبع"""
    if username:
        return f"https://t.me/{username}/{msg.id}"
    cid = str(msg.chat_id)
    internal = cid[4:] if cid.startswith("-100") else cid.lstrip("-")
    return f"https://t.me/c/{internal}/{msg.id}"


class Reposter:
    def __init__(self, client):
        self.client = client
        self.my_id = None
        self.sources = {}                 # chat_id -> {identifier, username, title}
        self.queue = asyncio.Queue()
        self.album_buf = defaultdict(list)
        self.album_timer = {}
        self.pending_text = set()         # رزرو موقت تا پایان ارسال
        self.pending_media = set()
        self.rate = defaultdict(deque)    # chat_id -> زمان‌های ارسال اخیر
        self.stats = {"sent": 0, "dup": 0, "skip": 0}
        self._dest_cache = None
        self._dest_cache_val = None
        self._worker_task = None

    # ------------------------------------------------------------
    # راه‌اندازی
    # ------------------------------------------------------------
    async def setup(self):
        me = await self.client.get_me()
        self.my_id = me.id
        self._load_sources()

        # منابع اولیه داخل config
        for ident in getattr(config, "SOURCE_CHANNELS", []):
            if not ident:
                continue
            try:
                await self.add_source_identifier(ident)
                log.info("Initial source added: %s", ident)
            except Exception as e:
                log.warning("Initial source not added (%s): %s", ident, e)

        # اگر مقصد داخل منابع بود، حذفش کن (جلوگیری از حلقه)
        dest_id = await self._dest_id_safe()
        if dest_id and dest_id in self.sources:
            removed = self.sources.pop(dest_id)
            db.remove_source(dest_id)
            log.warning("%s is the destination channel and can not be a source; removed from list",
                        removed.get("title"))

        self.client.add_event_handler(self.on_message, events.NewMessage())
        self.client.add_event_handler(
            self.on_command, events.NewMessage(chats="me", outgoing=True))
        self._worker_task = asyncio.create_task(self.worker())
        log.info("Bot ready - active sources: %d", len(self.sources))

    def _load_sources(self):
        for row in db.get_sources():
            self.sources[row["chat_id"]] = {
                "identifier": row["identifier"],
                "username": row["username"],
                "title": row["title"],
            }

    # ------------------------------------------------------------
    # کانال مقصد
    # ------------------------------------------------------------
    async def get_dest(self):
        val = db.get_setting("dest") or config.DEST_CHANNEL
        if not val:
            raise RuntimeError("کانال مقصد تنظیم نشده است")
        if self._dest_cache_val == val and self._dest_cache is not None:
            return self._dest_cache
        ent = await self.client.get_entity(val)
        self._dest_cache = ent
        self._dest_cache_val = val
        return ent

    async def _dest_id_safe(self):
        try:
            return tl_utils.get_peer_id(await self.get_dest())
        except Exception:
            return None

    # ------------------------------------------------------------
    # مدیریت منابع
    # ------------------------------------------------------------
    async def add_source_identifier(self, identifier):
        """اضافه‌کردن منبع با یوزرنیم/لینک/آیدی — عنوان کانال رو برمی‌گردونه"""
        identifier = (identifier or "").strip()
        if not identifier:
            raise ValueError("شناسه کانال خالی است")

        m = re.search(r"(?:t\.me/|telegram\.me/)(\w+)", identifier, re.I)
        if m and "joinchat" not in identifier and "+" not in identifier:
            identifier = "@" + m.group(1)
        if not identifier.startswith("@") and not identifier.lstrip("-").isdigit():
            identifier = "@" + identifier.lstrip("@")

        target = None
        try:
            target = await self.client.get_entity(identifier)
        except Exception:
            # شاید اکانت هنوز کانال رو ندیده؛ عضو شو بعد دوباره امتحان کن
            if identifier.startswith("@"):
                try:
                    await self.client(JoinChannelRequest(identifier))
                    target = await self.client.get_entity(identifier)
                except Exception:
                    pass
        if target is None:
            raise ValueError(
                f"کانال «{identifier}» پیدا نشد.\n"
                "اول با اکانتِ ربات یک بار کانال رو باز کن یا عضوش شو، بعد دوباره /add بزن.")

        chat_id = tl_utils.get_peer_id(target)
        if chat_id == self.my_id:
            raise ValueError("پیام‌های ذخیره‌شده نمی‌تونه منبع باشه.")
        if chat_id in self.sources:
            raise ValueError("این کانال قبلاً به منابع اضافه شده.")
        dest_id = await self._dest_id_safe()
        if dest_id and chat_id == dest_id:
            raise ValueError("این کانال، مقصده و نمی‌تونه هم‌زمان منبع باشه.")

        username = getattr(target, "username", None)
        title = getattr(target, "title", None) or identifier
        db.add_source(chat_id, identifier, username, title)
        self.sources[chat_id] = {
            "identifier": identifier, "username": username, "title": title,
        }
        return title

    # ------------------------------------------------------------
    # ورودی‌های جدید
    # ------------------------------------------------------------
    async def on_message(self, event):
        try:
            msg = event.message
            if msg is None or msg.action or event.out:
                return
            chat_id = msg.chat_id
            if chat_id not in self.sources:
                return
            dest_id = await self._dest_id_safe()
            if dest_id and chat_id == dest_id:
                return
            # پست‌های خیلی قدیمی (مثل جمع‌کردن عقب‌ماندگی بعد از قطعی) نادیده
            if msg.date and msg.date < datetime.now(timezone.utc) - timedelta(
                    minutes=config.MAX_MESSAGE_AGE_MINUTES):
                return
            if msg.grouped_id:
                self._buffer_album(msg)
            else:
                await self._process_single(msg)
        except Exception:
            log.exception("Error processing incoming message")

    # ------------------------------------------------------------
    # آلبوم‌ها (پست‌های چندمدیایی)
    # ------------------------------------------------------------
    def _buffer_album(self, msg):
        gid = msg.grouped_id
        self.album_buf[gid].append(msg)
        task = self.album_timer.get(gid)
        if task is None or task.done():
            self.album_timer[gid] = asyncio.create_task(self._delayed_flush(gid))

    async def _delayed_flush(self, gid):
        await asyncio.sleep(config.ALBUM_WAIT_SECONDS)
        self.album_timer.pop(gid, None)
        msgs = self.album_buf.pop(gid, None)
        if not msgs:
            return
        msgs.sort(key=lambda m: m.id)
        try:
            await self._process_album(msgs)
        except Exception:
            log.exception("Error processing album")

    async def _process_album(self, msgs):
        src = self.sources.get(msgs[0].chat_id)
        if not src:
            return
        if any(m.poll or m.contact or m.dice for m in msgs):
            log.info("Album with unsupported content type; skipped from %s", src["title"])
            self.stats["skip"] += 1
            return

        raw_text = next((m.message for m in msgs if m.message), "") or ""
        ents = next((m.entities for m in msgs if m.message and m.entities), None)
        text, ents = self._prepare_text(raw_text, ents)
        thash = _text_hash(text) if config.DEDUP_BY_TEXT else None

        uids = []
        for m in msgs:
            u = _media_uid(m) if _real_media(m) else None
            if u and config.DEDUP_BY_MEDIA:
                uids.append(u)

        if self._is_duplicate(thash, None) or self._any_media_dup(uids):
            self.stats["dup"] += 1
            log.info("Duplicate album skipped from %s", src["title"])
            return

        if not self._rate_ok(msgs[0].chat_id):
            self.stats["skip"] += 1
            log.info("Rate limit; album skipped from %s", src["title"])
            return

        self._reserve(thash, uids)
        link = _source_link(msgs[0], src.get("username"))
        await self.queue.put({
            "kind": "album", "msgs": msgs, "text": text, "entities": ents,
            "thash": thash, "uids": uids, "link": link,
            "source": src.get("title") or str(msgs[0].chat_id),
            "chat_id": msgs[0].chat_id, "msg_id": msgs[0].id, "bare": False,
        })

    # ------------------------------------------------------------
    # پیام‌های تکی
    # ------------------------------------------------------------
    async def _process_single(self, msg):
        src = self.sources.get(msg.chat_id)
        if not src:
            return

        raw_text = msg.message or ""
        media = _real_media(msg)
        uid = _media_uid(msg) if media else None

        # انواع پشتیبانی‌نشده یا پیام بدون محتوا
        if msg.poll or msg.contact or msg.dice or (not raw_text.strip() and not media):
            log.info("Message type is not copyable; skipped from %s", src["title"])
            self.stats["skip"] += 1
            return

        # این‌ها کپشن قبول نمی‌کنن
        bare = bool(msg.sticker or msg.voice or msg.video_note)

        text, ents = self._prepare_text(raw_text, msg.entities)
        if not text.strip() and not media:
            log.info("Post became empty after cleaning; skipped from %s", src["title"])
            self.stats["skip"] += 1
            return
        thash = _text_hash(text) if config.DEDUP_BY_TEXT else None
        if not config.DEDUP_BY_MEDIA:
            uid = None

        if self._is_duplicate(thash, uid):
            self.stats["dup"] += 1
            log.info("Duplicate post skipped from %s", src["title"])
            return

        if not self._rate_ok(msg.chat_id):
            self.stats["skip"] += 1
            log.info("Rate limit; post skipped from %s", src["title"])
            return

        uids = [uid] if uid else []
        self._reserve(thash, uids)
        link = _source_link(msg, src.get("username"))
        await self.queue.put({
            "kind": "single", "msgs": [msg], "text": text, "entities": ents,
            "thash": thash, "uids": uids, "link": link,
            "source": src.get("title") or str(msg.chat_id),
            "chat_id": msg.chat_id, "msg_id": msg.id, "bare": bare,
        })

    # ------------------------------------------------------------
    # ابزار ضد تکرار و محدودیت سرعت
    # ------------------------------------------------------------
    def _prepare_text(self, raw, entities):
        if not config.ENABLE_TEXT_CLEANING:
            return raw or "", list(entities or [])
        text, ents, ratio = clean_message_text(
            raw, entities,
            keywords=config.AD_KEYWORDS,
            remove_hashtag_lines=config.REMOVE_HASHTAG_LINES,
            max_ratio=config.CLEAN_MAX_RATIO,
        )
        return text, ents

    def _is_duplicate(self, thash, uid):
        if thash and (thash in self.pending_text or db.has_text(thash)):
            return True
        if uid and (uid in self.pending_media or db.has_media(uid)):
            return True
        return False

    def _any_media_dup(self, uids):
        for u in uids:
            if u in self.pending_media or db.has_media(u):
                return True
        return False

    def _reserve(self, thash, uids):
        if thash:
            self.pending_text.add(thash)
        for u in uids:
            if u:
                self.pending_media.add(u)

    def _release(self, thash, uids):
        if thash:
            self.pending_text.discard(thash)
        for u in uids:
            if u:
                self.pending_media.discard(u)

    def _rate_ok(self, chat_id):
        now = time.time()
        dq = self.rate[chat_id]
        while dq and now - dq[0] > 3600:
            dq.popleft()
        if len(dq) >= config.MAX_POSTS_PER_HOUR_PER_SOURCE:
            return False
        dq.append(now)
        return True

    # ------------------------------------------------------------
    # صف ارسال
    # ------------------------------------------------------------
    async def worker(self):
        log.info("Send queue started")
        while True:
            task = await self.queue.get()
            try:
                await self._send_task(task)
            except Exception:
                log.exception("Failed to send post from %s", task.get("source"))
            finally:
                self._release(task.get("thash"), task.get("uids") or [])
                await asyncio.sleep(config.MIN_SEND_INTERVAL)

    async def _send_task(self, task):
        try:
            dest = await self.get_dest()
        except Exception:
            log.error("Destination channel not found. Set it first with /setdest @channel.")
            return

        dest_msg = None
        for attempt in (1, 2):
            try:
                dest_msg = await self._send_copy(dest, task)
                break
            except FloodWaitError as e:
                wait = min(int(getattr(e, "seconds", 30)) + 1, 900)
                log.warning("Telegram FloodWait: waiting %s seconds", wait)
                await asyncio.sleep(wait)
            except Exception:
                log.exception("Send error (attempt %s)", attempt)
                if attempt == 1:
                    await asyncio.sleep(3)

        if dest_msg is not None:
            rows = task.get("uids") or [None]
            for i, u in enumerate(rows):
                db.mark_posted(
                    task.get("thash") if i == 0 else None,
                    u, task.get("chat_id"), task.get("msg_id"),
                    getattr(dest_msg, "id", None), task.get("source"),
                )
            self.stats["sent"] += 1
            log.info("Post published from %s", task.get("source"))

    async def _send_copy(self, dest, task):
        if task["kind"] == "album":
            return await self._send_album(dest, task)

        msg = task["msgs"][0]
        link = task["link"]
        source_btn = [Button.url(config.SOURCE_BUTTON_TEXT, link)] if link else None

        # استیکر/وویس/ویدیونوت: بدون کپشن؛ تلاش برای دکمه، در خطا بدون دکمه
        if task["bare"]:
            try:
                return await self.client.send_message(
                    dest, file=msg.media, buttons=source_btn, parse_mode=None)
            except Exception:
                return await self.client.send_message(
                    dest, file=msg.media, parse_mode=None)

        # پست متنی خالص
        if msg.media is None or _real_media(msg) is None:
            text, ents = fit_text(task["text"], task["entities"], MAX_TEXT)
            return await self.client.send_message(
                dest, text, formatting_entities=ents or None,
                buttons=source_btn, parse_mode=None, link_preview=True)

        # پست مدیادار (عکس/ویدیو/سند/گیف/آودیو)
        text, ents = fit_text(task["text"], task["entities"], MAX_CAPTION)
        return await self.client.send_message(
            dest, text, formatting_entities=ents or None,
            file=msg.media, buttons=source_btn, parse_mode=None)

    async def _send_album(self, dest, task):
        msgs = [m for m in task["msgs"] if _real_media(m)]
        if not msgs:
            return None
        link_line = "\n\n" + config.SOURCE_LINK_TEXT.format(link=task["link"])
        caption, _ = fit_text((task["text"] or "") + link_line, [], MAX_CAPTION)

        # تلاش ۱: کپی مستقیم مدیاها (بدون دانلود مجدد)
        try:
            files = [m.media for m in msgs]
            caps = [""] * len(files)
            caps[-1] = caption
            return await self.client.send_file(
                dest, files, caption=caps, parse_mode=None)
        except Exception:
            log.exception("Direct album copy failed; falling back to download/re-upload")

        # تلاش ۲: دانلود و آپلود مجدد
        tmpdir = tempfile.mkdtemp(prefix="repost_")
        try:
            paths = []
            for m in msgs:
                try:
                    p = await self.client.download_media(
                        m, file=os.path.join(tmpdir, str(m.id)))
                    if p:
                        paths.append(p)
                except Exception:
                    log.exception("Failed to download one of the album items")
            if not paths:
                return None
            caps = [""] * len(paths)
            caps[-1] = caption
            return await self.client.send_file(
                dest, paths, caption=caps, parse_mode=None)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    # ============================================================
    # دستورات تلگرام (فقط در «پیام‌های ذخیره‌شده» خودت)
    # ============================================================
    async def on_command(self, event):
        try:
            text = (event.raw_text or "").strip()
            if not text.startswith("/"):
                return
            parts = text.split()
            cmd = parts[0].split("@")[0].lower()
            table = {
                "/add": self.cmd_add,
                "/remove": self.cmd_remove,
                "/list": self.cmd_list,
                "/setdest": self.cmd_setdest,
                "/stats": self.cmd_stats,
                "/clear": self.cmd_clear,
                "/test": self.cmd_test,
                "/help": self.cmd_help,
                "/start": self.cmd_help,
            }
            fn = table.get(cmd)
            if fn is None:
                return
            await fn(event, parts[1:])
        except Exception:
            log.exception("Command execution error")
            try:
                await event.reply("❌ خطایی پیش آمد؛ لاگ ربات رو بررسی کن.", parse_mode=None)
            except Exception:
                pass

    async def cmd_add(self, event, args):
        # حالت ۱: ریپلای روی یک پیام فورواردشده از کانال (برای کانال‌های خصوصی)
        if event.is_reply:
            rmsg = await event.get_reply_message()
            fwd = getattr(rmsg, "forward", None) if rmsg else None
            chat = getattr(fwd, "chat", None) if fwd else None
            if chat is None:
                await event.reply(
                    "❌ پیامِ ریپلای‌شده، فوروارد از یک کانال نیست.", parse_mode=None)
                return
            chat_id = tl_utils.get_peer_id(chat)
            if chat_id in self.sources:
                await event.reply("⚠️ این کانال قبلاً اضافه شده.", parse_mode=None)
                return
            dest_id = await self._dest_id_safe()
            if dest_id and chat_id == dest_id:
                await event.reply("❌ کانال مقصد نمی‌تونه منبع باشه.", parse_mode=None)
                return
            username = getattr(chat, "username", None)
            title = getattr(chat, "title", None) or str(chat_id)
            db.add_source(chat_id, f"id:{chat_id}", username, title)
            self.sources[chat_id] = {
                "identifier": f"id:{chat_id}", "username": username, "title": title,
            }
            await event.reply(
                f"✅ کانال «{title}» به منابع اضافه شد.\n"
                f"هم‌اکنون {len(self.sources)} منبع فعال داری.", parse_mode=None)
            return

        # حالت ۲: /add @username
        if not args:
            await event.reply(
                "روش استفاده:\n"
                "• /add @channelname\n"
                "• /add https://t.me/channelname\n"
                "• یا روی یک پیام فورواردشده از کانال، ریپلای کن و بنویس /add",
                parse_mode=None)
            return
        try:
            title = await self.add_source_identifier(args[0])
            await event.reply(
                f"✅ کانال «{title}» به منابع اضافه شد.\n"
                f"هم‌اکنون {len(self.sources)} منبع فعال داری.", parse_mode=None)
        except ValueError as e:
            await event.reply(f"❌ {e}", parse_mode=None)

    async def cmd_remove(self, event, args):
        chat_id = None
        if event.is_reply:
            rmsg = await event.get_reply_message()
            fwd = getattr(rmsg, "forward", None) if rmsg else None
            chat = getattr(fwd, "chat", None) if fwd else None
            if chat is not None:
                chat_id = tl_utils.get_peer_id(chat)
        elif args:
            ident = args[0].strip()
            row = db.find_source_by_identifier(ident)
            if not row:
                m = re.search(r"(?:t\.me/|telegram\.me/)(\w+)", ident, re.I)
                if m:
                    row = db.find_source_by_identifier("@" + m.group(1))
            if row:
                chat_id = row["chat_id"]
            elif ident.lstrip("-").isdigit():
                chat_id = int(ident)

        if chat_id is None or chat_id not in self.sources:
            await event.reply(
                "❌ پیدا نشد. با /list لیست منابع رو ببین و همین‌طوری حذف کن:\n"
                "/remove @channelname", parse_mode=None)
            return
        title = self.sources.pop(chat_id).get("title") or str(chat_id)
        db.remove_source(chat_id)
        await event.reply(
            f"🗑 کانال «{title}» از منابع حذف شد.\nهم‌اکنون {len(self.sources)} منبع فعال داری.",
            parse_mode=None)

    async def cmd_list(self, event, args):
        if not self.sources:
            await event.reply(
                "📡 لیست منابع خالیه.\nبا /add @channel اولین منبع رو اضافه کن.",
                parse_mode=None)
            return
        lines = [f"📡 منابع فعال ({len(self.sources)} عدد):"]
        for i, (cid, s) in enumerate(
                sorted(self.sources.items(), key=lambda kv: kv[1].get("title") or ""), 1):
            uname = s.get("username")
            extra = f" — @{uname}" if uname else ""
            lines.append(f"{i}. {s.get('title') or cid}{extra}")
        dest = db.get_setting("dest") or config.DEST_CHANNEL
        lines.append("")
        lines.append(f"🎯 مقصد: {dest if dest else 'هنوز تنظیم نشده — با /setdest تنظیمش کن'}")
        await event.reply("\n".join(lines), parse_mode=None)

    async def cmd_setdest(self, event, args):
        if not args:
            await event.reply("روش استفاده:  /setdest @mychannel", parse_mode=None)
            return
        ident = args[0].strip()
        try:
            ent = await self.client.get_entity(ident)
            cid = tl_utils.get_peer_id(ent)
        except Exception:
            await event.reply(
                "❌ کانال مقصد پیدا نشد.\n"
                "مطمئن شو اکانت ربات عضو کاناله (برای ارسال باید ادمین باشی).",
                parse_mode=None)
            return
        if cid in self.sources:
            await event.reply(
                "❌ این کانال تو لیست منابع هست و نمی‌تونه مقصد باشه.\n"
                "اول با /remove حذفش کن.", parse_mode=None)
            return
        db.set_setting("dest", ident)
        self._dest_cache = None
        self._dest_cache_val = None
        await event.reply(
            f"🎯 مقصد روی «{getattr(ent, 'title', None) or ident}» تنظیم شد.",
            parse_mode=None)

    async def cmd_stats(self, event, args):
        lines = [
            "📊 آمار ربات بازنشر:",
            f"• منابع فعال: {len(self.sources)}",
            f"• کل پست‌های منتشرشده (تاریخچه): {db.count_posted()}",
            f"• در این اجرا: {self.stats['sent']} ارسال موفق، "
            f"{self.stats['dup']} تکراری ردشده، {self.stats['skip']} ردشده",
            f"• در صف انتظار: {self.queue.qsize()}",
        ]
        await event.reply("\n".join(lines), parse_mode=None)

    async def cmd_clear(self, event, args):
        if args and args[0].lower() == "yes":
            db.clear_history()
            await event.reply(
                "🧹 تاریخچه ضد تکرار پاک شد.\n"
                "از این به بعد پست‌های همسان ممکنه دوباره منتشر بشن.",
                parse_mode=None)
        else:
            await event.reply(
                "⚠️ این کار کل تاریخچه ضد تکرار رو پاک می‌کنه و پست‌های قبلی "
                "ممکنه دوباره ارسال بشن.\nبرای تایید بفرست:  /clear yes",
                parse_mode=None)

    async def cmd_test(self, event, args):
        try:
            dest = await self.get_dest()
        except Exception:
            await event.reply(
                "❌ مقصد هنوز تنظیم نشده. با /setdest @mychannel تنظیمش کن.",
                parse_mode=None)
            return
        try:
            await self.client.send_message(
                dest, "✅ پست آزمایشی ربات بازنشر — اتصال سالمه!", parse_mode=None)
            await event.reply("✅ پست آزمایشی با موفقیت به مقصد ارسال شد.", parse_mode=None)
        except Exception as e:
            await event.reply(f"❌ ارسال به مقصد ناموفق بود:\n{e}", parse_mode=None)

    async def cmd_help(self, event, args):
        await event.reply(HELP_TEXT, parse_mode=None)


HELP_TEXT = (
    "🤖 راهنمای ربات بازنشر\n"
    "——————————————————\n"
    "/add @channelname — افزودن کانال منبع (عضویت خودکار)\n"
    "/add روی پیام فورواردشده — افزودن کانال خصوصی\n"
    "/remove @channelname — حذف کانال منبع\n"
    "/list — لیست منابع و مقصد\n"
    "/setdest @mychannel — تنظیم کانال مقصد\n"
    "/test — ارسال پست آزمایشی به مقصد\n"
    "/stats — آمار و وضعیت ربات\n"
    "/clear yes — پاک‌کردن تاریخچه ضد تکرار\n"
    "/help — همین راهنما\n"
    "——————————————————\n"
    "💡 نکته‌ها:\n"
    "• اکانتِ ربات باید عضو کانال‌های منبع باشه (برای کانال عمومی، /add خودش عضو می‌شه).\n"
    "• اکانت ربات باید در کانال مقصد دسترسی ارسال داشته باشه.\n"
    "• پست تکراری (متن یا فایل همسان) خودکار رد می‌شه.\n"
    "• زیر هر پست، دکمه «منبع» می‌ذاره؛ برای آلبوم‌ها لینک متنی آخر کپشن می‌ره."
)
