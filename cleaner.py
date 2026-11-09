# -*- coding: utf-8 -*-
"""
پاکسازی هوشمند متن پست‌ها:
- حذف خط‌های تبلیغی (لینک t.me، @آیدی، #هشتگ، کلیدواژه تبلیغ)
- فشرده‌سازی خطوط خالی باقیمانده بعد از حذف
- حفظ فرمت متن (بولد، ایتالیک، لینک و ...) با جابه‌جایی درست آفست موجودیت‌ها
- محافظت: اگه حذف‌ها بیش از حد مجاز باشه، متن دست‌نخورده برمی‌گرده
"""

import re

from telethon.tl.types import (
    MessageEntityTextUrl,
    MessageEntityPre,
    MessageEntityCustomEmoji,
    MessageEntityMentionName,
)

# جداکننده‌های خط که splitlines روی اونا هم می‌شکنه
_SEPS = "\r\n\u2028\u2029\x85\x0b\x0c"

_LINK_HINT = re.compile(r"(t\.me|telegram\.me|https?://|@|#)", re.IGNORECASE)


def _is_ad_line(line, keywords, remove_hashtag_lines):
    """تشخیص اینکه یک خط، خط تبلیغی هست یا نه"""
    s = line.strip()
    if not s:
        return False

    # الگوی توکن‌های تبلیغ
    pattern = r"(https?://\S+|t\.me/\S+|telegram\.me/\S+|@\w+)"
    if remove_hashtag_lines:
        pattern += r"|#\w+"

    # حذف توکن‌های لینک و علائم و ایموجی؛ اگه هیچ حرف واقعی نمونده
    # یعنی خط، خط تبلیغی خالص بوده
    residue = re.sub(pattern, " ", s, flags=re.IGNORECASE)
    residue = re.sub(r"[^\w\s\u0600-\u06FF]", " ", residue)
    residue = re.sub(r"[_\d]+", " ", residue)
    residue = residue.strip()
    if not re.search(r"[\u0600-\u06FFa-zA-Z]", residue):
        return True

    # خط‌های کلیدواژه‌دار: کوتاه، یا با لینک/منشن/هشتگ (تا ۱۲۰ کاراکتر)
    low = s.lower()
    has_link = bool(_LINK_HINT.search(s))
    for kw in keywords:
        k = str(kw).lower().strip()
        if not k:
            continue
        if k in low and (len(s) <= 60 or (has_link and len(s) <= 120)):
            return True
    return False


def _add_removal(removals, a, b):
    """افزودن بازه (شروع، پایان) با ادغام کامل بازه‌های همپوشان"""
    if b <= a:
        return
    removals.append((a, b))
    removals.sort()
    merged = []
    for x, y in removals:
        if merged and x <= merged[-1][1]:
            px, py = merged[-1]
            merged[-1] = (px, max(py, y))
        else:
            merged.append((x, y))
    removals[:] = merged


def _find_ad_removals(text, keywords, remove_hashtag_lines):
    """بازه‌های خط‌های تبلیغی روی مختصات متن اصلی"""
    removals = []
    pos = 0
    for line in text.splitlines(keepends=True):
        core = line.rstrip(_SEPS)
        if core.strip() and _is_ad_line(core, keywords, remove_hashtag_lines):
            _add_removal(removals, pos, pos + len(core))
        pos += len(line)
    return removals


def _collapse_removals(text):
    """بازه‌های فشرده‌سازی خط‌های خالی + پاکسازی اول/آخر متن (روی متن میانی)"""
    removals = []

    lead = len(text) - len(text.lstrip())
    if lead:
        _add_removal(removals, 0, lead)
    tstart = len(text.rstrip())
    if tstart < len(text):
        _add_removal(removals, tstart, len(text))
    for m in re.finditer(r"\n{3,}", text):
        _add_removal(removals, m.start() + 2, m.end())

    return removals


# ---------------- ابزار آفست UTF-16 ----------------
# تلگرام آفست موجودیت‌ها رو با واحد UTF-16 حساب می‌کنه؛
# ایموجی‌ها ۲ واحد و بقیه کاراکترها ۱ واحد هستن.

def _u16_to_cp_map(text):
    """نگاشت: اندازه UTF-16  ->  اندازه کاراکتر پایتون (None داخل ایموجی)"""
    m = []
    for i, ch in enumerate(text):
        m.append(i)
        if ord(ch) > 0xFFFF:
            m.append(None)
    m.append(len(text))
    return m


def _cp_start(mapv, u16pos):
    if u16pos >= len(mapv):
        return mapv[-1]
    v = mapv[u16pos]
    while v is None and u16pos > 0:
        u16pos -= 1
        v = mapv[u16pos]
    return v if v is not None else 0


def _cp_end(mapv, u16pos):
    if u16pos >= len(mapv):
        return mapv[-1]
    v = mapv[u16pos]
    while v is None and u16pos < len(mapv) - 1:
        u16pos += 1
        v = mapv[u16pos]
    return v if v is not None else mapv[-1]


def _cp_to_u16(text, cp):
    if cp <= 0:
        return 0
    if cp >= len(text):
        return len(text.encode("utf-16-le")) // 2
    return len(text[:cp].encode("utf-16-le")) // 2


def _rebuild_entity(e, offset, length):
    """همون موجودیت رو با آفست جدید می‌سازه"""
    cls = type(e)
    if cls is MessageEntityTextUrl:
        return MessageEntityTextUrl(offset=offset, length=length, url=e.url)
    if cls is MessageEntityPre:
        return MessageEntityPre(offset=offset, length=length, language=e.language or "")
    if cls is MessageEntityCustomEmoji:
        return MessageEntityCustomEmoji(offset=offset, length=length, document_id=e.document_id)
    if cls is MessageEntityMentionName:
        return MessageEntityMentionName(offset=offset, length=length, user_id=e.user_id)
    return cls(offset=offset, length=length)


def _build(text, keep):
    """متنِ فقط-کاراکترهای-نگه‌داشته + نگاشت اندیس قدیم -> اندیس جدید"""
    buf = []
    newpos = {}
    j = 0
    for i, k in enumerate(keep):
        if k:
            newpos[i] = j
            buf.append(text[i])
            j += 1
    return "".join(buf), newpos


def _apply_keep_mask(text, ranges):
    keep = [True] * len(text)
    for a, b in ranges:
        for i in range(a, min(b, len(text))):
            keep[i] = False
    return keep


def clean_message_text(text, entities=None, keywords=None,
                       remove_hashtag_lines=True, max_ratio=0.7):
    """
    پاکسازی متن پیام تلگرام.

    ورودی:
        text            متن اصلی پیام (str)
        entities        لیست موجودیت‌های قالب‌بندی تلگرام (اختیاری)
        keywords        کلیدواژه‌های تبلیغاتی (لیست)
        remove_hashtag_lines  حذف خط‌هایی که فقط هشتگ/لینک/منشن دارن
        max_ratio       نسبت مجاز حذف (محافظت از متن اصلی)

    خروجی:
        (متن_تمیز, موجودیت‌های_اصلاح‌شده, نسبت_حذف‌شده)
    """
    text = text or ""
    keywords = keywords or []
    entities = list(entities or [])

    if not text.strip():
        return text, entities, 0.0

    # ---------- مرحله ۱: پیدا کردن خط‌های تبلیغی ----------
    removals = _find_ad_removals(text, keywords, remove_hashtag_lines)
    if not removals:
        return text, entities, 0.0

    removed_len = sum(b - a for a, b in removals)
    ratio = removed_len / max(len(text), 1)
    if ratio > max_ratio:
        # حذف خیلی زیاد بود؛ محتاط می‌شیم و متن اصلی رو نگه می‌داریم
        return text, entities, ratio

    # ---------- مرحله ۲: حذف بازه‌ها ----------
    keep1 = _apply_keep_mask(text, removals)
    inter_text, map1 = _build(text, keep1)

    # ---------- مرحله ۳: فشرده‌سازی خط‌های خالی روی متن میانی ----------
    final_text = inter_text
    map2 = None
    collapse = _collapse_removals(inter_text) if inter_text else []
    if collapse:
        keep2 = _apply_keep_mask(inter_text, collapse)
        final_text, map2 = _build(inter_text, keep2)

    # نگاشت ترکیبی: اندیس متن اصلی -> اندیس متن نهایی
    if map2 is not None:
        finalpos = {}
        for old, mid in map1.items():
            fin = map2.get(mid)
            if fin is not None:
                finalpos[old] = fin
    else:
        finalpos = map1

    # ---------- مرحله ۴: اصلاح آفست موجودیت‌ها ----------
    new_entities = []
    if entities:
        mapv = _u16_to_cp_map(text)
        for e in entities:
            s = _cp_start(mapv, e.offset)
            en = _cp_end(mapv, e.offset + e.length)
            if en <= s:
                continue
            ns = ne = None
            for i in range(s, en):
                if i in finalpos:
                    ns = finalpos[i]
                    break
            for i in range(en - 1, s - 1, -1):
                if i in finalpos:
                    ne = finalpos[i]
                    break
            if ns is None or ne is None or ne < ns:
                continue  # موجودیت کاملاً داخل بخش حذف‌شده بود
            u16_s = _cp_to_u16(final_text, ns)
            u16_e = _cp_to_u16(final_text, ne + 1)
            if u16_e <= u16_s:
                continue
            new_entities.append(_rebuild_entity(e, u16_s, u16_e - u16_s))

    return final_text, new_entities, ratio


def fit_text(text, entities, limit):
    """کوتاه‌کردن متن به سقف مجاز (۴۰۹۶ برای متن، ۱۰۲۴ برای کپشن)"""
    text = text or ""
    if len(text) <= limit:
        return text, entities
    cut = limit - 1
    new_text = text[:cut] + "…"
    mapv = _u16_to_cp_map(text)
    kept = []
    for e in (entities or []):
        s = _cp_start(mapv, e.offset)
        if s is not None and s < cut:
            kept.append(e)
    return new_text, kept
