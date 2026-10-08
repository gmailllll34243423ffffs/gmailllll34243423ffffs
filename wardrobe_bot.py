#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WardrobeBot v2 — разбор гардероба, луки, статистика стиля.
Запуск: set BOT_TOKEN=xxx & python wardrobe_bot.py
Требуется: pip install aiogram
"""
import asyncio
import csv
import io
import json
import logging
import os
import random
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

BOT_TOKEN = os.getenv("BOT_TOKEN", "ВСТАВЬ_ТОКЕН_СЮДА")
DB_PATH = os.getenv("WARDROBE_DB", "wardrobe_v2.db")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
PROXY_URL = os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY") or os.getenv("http_proxy") or os.getenv("https_proxy") or ""

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("wardrobe_v2")

# ---------------------------------------------------------------------------
# CONSTANTS
# ---------------------------------------------------------------------------

CATEGORIES = [
    ("top", "Верх"),
    ("bottom", "Низ"),
    ("shoes", "Обувь"),
    ("outer", "Верхняя одежда"),
    ("dress", "Платье/комбез"),
    ("access", "Аксессуар"),
    ("sport", "Спорт"),
    ("home", "Домашнее"),
]

SEASONS = [
    ("all", "Всесезон"),
    ("summer", "Лето"),
    ("winter", "Зима"),
    ("demi", "Весна/Осень"),
]

COLORS = [
    ("black", "Чёрный"),
    ("white", "Белый"),
    ("grey", "Серый"),
    ("blue", "Синий"),
    ("red", "Красный"),
    ("green", "Зелёный"),
    ("beige", "Бежевый"),
    ("brown", "Коричневый"),
    ("yellow", "Жёлтый"),
    ("pink", "Розовый"),
    ("multi", "Мульти"),
]

STYLES = [
    ("casual", "Кэжуал"),
    ("office", "Офис"),
    ("street", "Стрит"),
    ("sport", "Спорт"),
    ("evening", "Вечер"),
    ("minimal", "Минимализм"),
]

# совместимость категорий в луке: что с чем сочетается
LOOK_SLOTS = ["top", "bottom", "shoes"]
OPTIONAL_SLOTS = ["outer", "access"]

STYLE_WEIGHTS: Dict[str, Dict[str, int]] = {
    "casual": {"top": 3, "bottom": 3, "shoes": 2, "outer": 2, "access": 1, "dress": 2},
    "office": {"top": 3, "bottom": 3, "shoes": 3, "outer": 1, "access": 2, "dress": 3},
    "street": {"top": 2, "bottom": 2, "shoes": 3, "outer": 3, "access": 3, "dress": 1},
    "sport": {"top": 2, "bottom": 2, "shoes": 3, "sport": 5, "outer": 1, "access": 0},
    "evening": {"top": 2, "bottom": 1, "shoes": 3, "dress": 5, "access": 3, "outer": 0},
    "minimal": {"top": 3, "bottom": 3, "shoes": 2, "outer": 1, "access": 0, "dress": 2},
}

COLOR_MATCH: Dict[str, List[str]] = {
    "black": ["black", "white", "grey", "blue", "red", "beige", "multi"],
    "white": ["black", "blue", "grey", "beige", "brown", "red", "multi"],
    "grey": ["black", "white", "blue", "pink", "multi"],
    "blue": ["white", "black", "grey", "beige", "brown", "multi"],
    "red": ["black", "white", "blue", "grey", "multi"],
    "green": ["black", "white", "beige", "brown", "multi"],
    "beige": ["white", "brown", "black", "blue", "green", "multi"],
    "brown": ["beige", "white", "black", "green", "multi"],
    "yellow": ["black", "blue", "grey", "multi"],
    "pink": ["grey", "white", "black", "blue", "multi"],
    "multi": ["black", "white", "grey", "blue", "red", "green", "beige", "brown", "yellow", "pink", "multi"],
}

HELP_TEXT = (
    "Что умею:\n"
    "— сохранять шмот по фото\n"
    "— разбирать по категориям / сезонам / цветам\n"
    "— собирать лук дня, умный лук по стилю\n"
    "— избранное, история, статистика\n\n"
    "Как добавить: просто кинь фото, дальше проведу за руку.\n"
    "Команды:\n"
    "/add — добавить вещь\n"
    "/wardrobe — шкаф\n"
    "/look — случайный лук\n"
    "/smart — умный лук по стилю\n"
    "/favs — избранное\n"
    "/history — история луков\n"
    "/stats — статистика\n"
    "/search — поиск по тегам\n"
    "/profile — стиль и сезон\n"
    "/export — выгрузка JSON\n"
    "/clear — очистить шкаф\n"
    "/cancel — отмена ввода"
)

MAIN_KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="➕ Добавить"), KeyboardButton(text="👕 Шкаф")],
        [KeyboardButton(text="🎲 Лук"), KeyboardButton(text="🧠 Умный лук")],
        [KeyboardButton(text="⭐ Избранное"), KeyboardButton(text="📊 Стата")],
        [KeyboardButton(text="🔍 Поиск"), KeyboardButton(text="👤 Профиль")],
    ],
    resize_keyboard=True,
    input_field_placeholder="Кинь фото шмота...",
)

# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  user_id INTEGER PRIMARY KEY,
  username TEXT DEFAULT '',
  style TEXT DEFAULT 'casual',
  season TEXT DEFAULT 'all',
  created_at TEXT DEFAULT '',
  looks_count INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS items(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  file_id TEXT NOT NULL,
  category TEXT DEFAULT 'top',
  season TEXT DEFAULT 'all',
  color TEXT DEFAULT 'multi',
  style TEXT DEFAULT 'casual',
  tags TEXT DEFAULT '',
  wear_count INTEGER DEFAULT 0,
  fav INTEGER DEFAULT 0,
  created_at TEXT DEFAULT '',
  UNIQUE(user_id, file_id, tags)
);
CREATE INDEX IF NOT EXISTS idx_items_user ON items(user_id);
CREATE INDEX IF NOT EXISTS idx_items_cat ON items(user_id, category);
CREATE INDEX IF NOT EXISTS idx_items_season ON items(user_id, season);
CREATE TABLE IF NOT EXISTS looks(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  item_ids TEXT NOT NULL,
  style TEXT DEFAULT 'casual',
  rating INTEGER DEFAULT 0,
  created_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS history(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  action TEXT DEFAULT '',
  detail TEXT DEFAULT '',
  created_at TEXT DEFAULT ''
);
"""


def db_connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def db_init() -> None:
    con = db_connect()
    cur = con.cursor()
    for stmt in SCHEMA.strip().split(";"):
        s = stmt.strip()
        if s:
            cur.execute(s)
    con.commit()
    con.close()
    log.info("db ready: %s", DB_PATH)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def ensure_user(user_id: int, username: str = "") -> dict:
    con = db_connect()
    cur = con.cursor()
    cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    if row is None:
        cur.execute(
            "INSERT INTO users(user_id, username, style, season, created_at, looks_count) VALUES (?,?,?,?,?,0)",
            (user_id, username or "", "casual", "all", now_iso()),
        )
        con.commit()
        cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
    con.close()
    return dict(row)


def update_profile(user_id: int, style: Optional[str] = None, season: Optional[str] = None) -> None:
    con = db_connect()
    cur = con.cursor()
    if style:
        cur.execute("UPDATE users SET style=? WHERE user_id=?", (style, user_id))
    if season:
        cur.execute("UPDATE users SET season=? WHERE user_id=?", (season, user_id))
    con.commit()
    con.close()


def add_history(user_id: int, action: str, detail: str = "") -> None:
    try:
        con = db_connect()
        cur = con.cursor()
        cur.execute(
            "INSERT INTO history(user_id, action, detail, created_at) VALUES (?,?,?,?)",
            (user_id, action, detail[:500], now_iso()),
        )
        con.commit()
        con.close()
    except Exception as e:
        log.warning("history fail: %s", e)


def item_add(user_id: int, file_id: str, category: str, season: str, color: str, style: str, tags: str) -> int:
    con = db_connect()
    cur = con.cursor()
    cur.execute(
        "INSERT INTO items(user_id, file_id, category, season, color, style, tags, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (user_id, file_id, category, season, color, style, tags[:300], now_iso()),
    )
    item_id = cur.lastrowid
    con.commit()
    con.close()
    add_history(user_id, "add", f"{category}/{color}/{tags}")
    return int(item_id)


def item_get(item_id: int, user_id: int) -> Optional[dict]:
    con = db_connect()
    cur = con.cursor()
    cur.execute("SELECT * FROM items WHERE id=? AND user_id=?", (item_id, user_id))
    r = cur.fetchone()
    con.close()
    return dict(r) if r else None


def item_delete(item_id: int, user_id: int) -> bool:
    con = db_connect()
    cur = con.cursor()
    cur.execute("DELETE FROM items WHERE id=? AND user_id=?", (item_id, user_id))
    n = cur.rowcount
    con.commit()
    con.close()
    if n:
        add_history(user_id, "del", str(item_id))
    return n > 0


def item_toggle_fav(item_id: int, user_id: int) -> Optional[int]:
    it = item_get(item_id, user_id)
    if not it:
        return None
    nv = 0 if it["fav"] else 1
    con = db_connect()
    cur = con.cursor()
    cur.execute("UPDATE items SET fav=? WHERE id=? AND user_id=?", (nv, item_id, user_id))
    con.commit()
    con.close()
    return nv


def item_bump_wear(item_id: int) -> None:
    try:
        con = db_connect()
        cur = con.cursor()
        cur.execute("UPDATE items SET wear_count=wear_count+1 WHERE id=?", (item_id,))
        con.commit()
        con.close()
    except Exception:
        pass


def items_list(
    user_id: int,
    category: Optional[str] = None,
    season: Optional[str] = None,
    color: Optional[str] = None,
    fav_only: bool = False,
    query: str = "",
    limit: int = 200,
    offset: int = 0,
) -> Tuple[List[dict], int]:
    con = db_connect()
    cur = con.cursor()
    where = ["user_id=?"]
    args: List[Any] = [user_id]
    if category and category != "allcat":
        where.append("category=?")
        args.append(category)
    if season and season != "all":
        where.append("(season=? OR season='all')")
        args.append(season)
    if color:
        where.append("color=?")
        args.append(color)
    if fav_only:
        where.append("fav=1")
    if query:
        where.append("tags LIKE ?")
        args.append(f"%{query}%")
    w = " AND ".join(where)
    cur.execute(f"SELECT COUNT(*) c FROM items WHERE {w}", args)
    total = int(cur.fetchone()["c"])
    cur.execute(f"SELECT * FROM items WHERE {w} ORDER BY id DESC LIMIT ? OFFSET ?", args + [limit, offset])
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return rows, total


def items_by_ids(user_id: int, ids: List[int]) -> List[dict]:
    if not ids:
        return []
    q = ",".join("?" for _ in ids)
    con = db_connect()
    cur = con.cursor()
    cur.execute(f"SELECT * FROM items WHERE user_id=? AND id IN ({q})", [user_id] + ids)
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    # сохранить порядок
    mp = {r["id"]: r for r in rows}
    return [mp[i] for i in ids if i in mp]


def look_save(user_id: int, item_ids: List[int], style: str) -> int:
    con = db_connect()
    cur = con.cursor()
    cur.execute(
        "INSERT INTO looks(user_id, item_ids, style, created_at) VALUES (?,?,?,?)",
        (user_id, json.dumps(item_ids), style, now_iso()),
    )
    lid = int(cur.lastrowid)
    cur.execute("UPDATE users SET looks_count=looks_count+1 WHERE user_id=?", (user_id,))
    for iid in item_ids:
        cur.execute("UPDATE items SET wear_count=wear_count+1 WHERE id=?", (iid,))
    con.commit()
    con.close()
    add_history(user_id, "look", ",".join(map(str, item_ids)))
    return lid


def look_rate(look_id: int, user_id: int, rating: int) -> None:
    con = db_connect()
    cur = con.cursor()
    cur.execute("UPDATE looks SET rating=? WHERE id=? AND user_id=?", (rating, look_id, user_id))
    con.commit()
    con.close()


def looks_history(user_id: int, limit: int = 10) -> List[dict]:
    con = db_connect()
    cur = con.cursor()
    cur.execute("SELECT * FROM looks WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit))
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return rows


def stats_for(user_id: int) -> dict:
    con = db_connect()
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) c FROM items WHERE user_id=?", (user_id,))
    total = int(cur.fetchone()["c"])
    cur.execute("SELECT category, COUNT(*) c FROM items WHERE user_id=? GROUP BY category", (user_id,))
    by_cat = {r["category"]: r["c"] for r in cur.fetchall()}
    cur.execute("SELECT color, COUNT(*) c FROM items WHERE user_id=? GROUP BY color", (user_id,))
    by_color = {r["color"]: r["c"] for r in cur.fetchall()}
    cur.execute("SELECT COUNT(*) c FROM items WHERE user_id=? AND fav=1", (user_id,))
    favs = int(cur.fetchone()["c"])
    cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    u = cur.fetchone()
    looks_count = int(u["looks_count"]) if u else 0
    cur.execute("SELECT tags FROM items WHERE user_id=?", (user_id,))
    tag_counter: Dict[str, int] = {}
    for r in cur.fetchall():
        for t in split_tags(r["tags"]):
            tag_counter[t] = tag_counter.get(t, 0) + 1
    top_tags = sorted(tag_counter.items(), key=lambda x: -x[1])[:10]
    con.close()
    return {"total": total, "by_cat": by_cat, "by_color": by_color, "favs": favs, "looks": looks_count, "top_tags": top_tags}


def export_json(user_id: int) -> str:
    rows, _ = items_list(user_id, limit=5000)
    data = [
        {"id": r["id"], "category": r["category"], "season": r["season"], "color": r["color"],
         "style": r["style"], "tags": r["tags"], "fav": r["fav"], "wear": r["wear_count"]}
        for r in rows
    ]
    return json.dumps({"exported_at": now_iso(), "count": len(data), "items": data}, ensure_ascii=False, indent=2)


def wipe_user(user_id: int) -> None:
    con = db_connect()
    cur = con.cursor()
    cur.execute("DELETE FROM items WHERE user_id=?", (user_id,))
    cur.execute("DELETE FROM looks WHERE user_id=?", (user_id,))
    cur.execute("DELETE FROM history WHERE user_id=?", (user_id,))
    cur.execute("UPDATE users SET looks_count=0 WHERE user_id=?", (user_id,))
    con.commit()
    con.close()

# ---------------------------------------------------------------------------
# UTILS
# ---------------------------------------------------------------------------

TAG_SPLIT_RE = re.compile(r"[#,;.\n]+")
SPACE_RE = re.compile(r"\s+")


def split_tags(s: str) -> List[str]:
    if not s:
        return []
    s = TAG_SPLIT_RE.sub(" ", s.lower())
    s = SPACE_RE.sub(" ", s).strip()
    parts = [p.strip(" ,") for p in s.split(",") if p.strip()]
    if len(parts) <= 1:
        parts = [p for p in s.split(" ") if p]
    # чистка мусора
    out = []
    for p in parts:
        p = p.strip()[:30]
        if len(p) >= 2:
            out.append(p)
    return out[:12]


def norm_tags(s: str) -> str:
    return ", ".join(split_tags(s))


def label(mapping: List[Tuple[str, str]], code: str) -> str:
    for k, v in mapping:
        if k == code:
            return v
    return code


def item_title(it: dict) -> str:
    return f"#{it['id']} {label(CATEGORIES, it['category'])} • {label(COLORS, it['color'])} • {label(SEASONS, it['season'])}"


def item_caption(it: dict) -> str:
    fav = " ⭐" if it["fav"] else ""
    return (
        f"{item_title(it)}{fav}\n"
        f"Стиль: {label(STYLES, it['style'])}\n"
        f"Теги: {it['tags'] or '—'}\n"
        f"Носкость: {it['wear_count']}"
    )


def score_item(it: dict, style: str, season: str, base_color: Optional[str] = None) -> float:
    s = 0.0
    # стиль вещи
    if it["style"] == style:
        s += 3.0
    # вес категории под стиль
    s += STYLE_WEIGHTS.get(style, {}).get(it["category"], 1) * 0.7
    # сезон
    if season == "all" or it["season"] in ("all", season):
        s += 2.0
    else:
        s -= 2.0
    # цветосочетание
    if base_color:
        if it["color"] in COLOR_MATCH.get(base_color, []):
            s += 1.5
        else:
            s -= 0.5
    # заношенность — свежие чуть выше, чтобы ротировать
    s -= min(it["wear_count"] * 0.25, 3.0)
    # избранное бустим
    if it["fav"]:
        s += 1.0
    # рандом для живости
    s += random.random()
    return s


def build_smart_look(user_id: int, style: str, season: str) -> List[dict]:
    rows, _ = items_list(user_id, limit=2000)
    if not rows:
        return []
    # если есть платье — иногда собираем вокруг него
    dresses = [r for r in rows if r["category"] == "dress"]
    use_dress = bool(dresses) and random.random() < 0.35
    picked: List[dict] = []
    used_ids = set()
    base_color: Optional[str] = None

    def pick_from(pool: List[dict]) -> Optional[dict]:
        nonlocal base_color
        if not pool:
            return None
        scored = sorted(pool, key=lambda r: score_item(r, style, season, base_color), reverse=True)
        # топ-3 чтобы не повторяться
        top = scored[:3]
        choice = random.choice(top)
        base_color = base_color or choice["color"]
        return choice

    if use_dress:
        d = pick_from(dresses)
        if d:
            picked.append(d)
            used_ids.add(d["id"])
            # к платью только обувь + аксессуар
            shoes = [r for r in rows if r["category"] == "shoes" and r["id"] not in used_ids]
            sh = pick_from(shoes)
            if sh:
                picked.append(sh)
                used_ids.add(sh["id"])
            acc = [r for r in rows if r["category"] == "access" and r["id"] not in used_ids]
            if acc and random.random() < 0.6:
                a = pick_from(acc)
                if a:
                    picked.append(a)
            return picked

    for slot in LOOK_SLOTS:
        pool = [r for r in rows if r["category"] == slot and r["id"] not in used_ids]
        # спорт-стиль может тянуть sport-категорию вместо top/bottom
        if not pool and style == "sport" and slot in ("top", "bottom"):
            pool = [r for r in rows if r["category"] == "sport" and r["id"] not in used_ids]
        c = pick_from(pool)
        if c:
            picked.append(c)
            used_ids.add(c["id"])

    # опционально верхняя одежда / аксессуар
    if random.random() < 0.5:
        pool = [r for r in rows if r["category"] in OPTIONAL_SLOTS and r["id"] not in used_ids]
        c = pick_from(pool)
        if c:
            picked.append(c)

    return picked


def build_random_look(user_id: int, season: str) -> List[dict]:
    rows, _ = items_list(user_id, limit=2000)
    if len(rows) < 2:
        return rows
    if season != "all":
        filt = [r for r in rows if r["season"] in ("all", season)]
        if len(filt) >= 2:
            rows = filt
    # пытаемся собрать верх+низ+обувь если есть
    by_cat: Dict[str, List[dict]] = {}
    for r in rows:
        by_cat.setdefault(r["category"], []).append(r)
    out: List[dict] = []
    for slot in LOOK_SLOTS:
        if slot in by_cat and by_cat[slot]:
            out.append(random.choice(by_cat[slot]))
    if not out:
        out = random.sample(rows, min(3, len(rows)))
    return out

# ---------------------------------------------------------------------------
# KEYBOARDS
# ---------------------------------------------------------------------------

def kb_categories(prefix: str = "cat") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for code, name in CATEGORIES:
        b.button(text=name, callback_data=f"{prefix}:{code}")
    b.adjust(2)
    return b.as_markup()


def kb_seasons(prefix: str = "sea") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for code, name in SEASONS:
        b.button(text=name, callback_data=f"{prefix}:{code}")
    b.adjust(2)
    return b.as_markup()


def kb_colors(prefix: str = "col") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for code, name in COLORS:
        b.button(text=name, callback_data=f"{prefix}:{code}")
    b.adjust(3)
    return b.as_markup()


def kb_styles(prefix: str = "sty") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for code, name in STYLES:
        b.button(text=name, callback_data=f"{prefix}:{code}")
    b.adjust(2)
    return b.as_markup()


def kb_item_card(item_id: int, is_fav: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="⭐ Убрать" if is_fav else "⭐ В избранное", callback_data=f"fav:{item_id}")
    b.button(text="🗑 Удалить", callback_data=f"del:{item_id}")
    b.button(text="🔁 Другой лук", callback_data="look:again")
    b.adjust(2)
    return b.as_markup()


def kb_look_actions(look_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="👍", callback_data=f"rate:{look_id}:5")
    b.button(text="👎", callback_data=f"rate:{look_id}:1")
    b.button(text="🎲 Ещё", callback_data="look:again")
    b.button(text="🧠 Умный", callback_data="look:smart")
    b.adjust(4)
    return b.as_markup()


def kb_pager(page: int, total_pages: int, prefix: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if page > 0:
        b.button(text="◀", callback_data=f"{prefix}:{page-1}")
    b.button(text=f"{page+1}/{max(total_pages,1)}", callback_data="noop")
    if page + 1 < total_pages:
        b.button(text="▶", callback_data=f"{prefix}:{page+1}")
    return b.as_markup()


def kb_profile() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="Стиль", callback_data="prof:style")
    b.button(text="Сезон", callback_data="prof:season")
    b.button(text="Статистика", callback_data="prof:stats")
    b.adjust(2)
    return b.as_markup()

# ---------------------------------------------------------------------------
# FSM
# ---------------------------------------------------------------------------

class AddItem(StatesGroup):
    waiting_photo = State()
    waiting_category = State()
    waiting_season = State()
    waiting_color = State()
    waiting_style = State()
    waiting_tags = State()
    confirm = State()


class SearchState(StatesGroup):
    waiting_query = State()


PAGE_SIZE = 5

# временный стор для пейджера: user_id -> list[item_id]
pager_cache: Dict[int, List[int]] = {}

# ---------------------------------------------------------------------------
# BOT
# ---------------------------------------------------------------------------

_session = AiohttpSession(proxy=PROXY_URL) if PROXY_URL else AiohttpSession()
bot = Bot(token=BOT_TOKEN, session=_session)
dp = Dispatcher(storage=MemoryStorage())

# ---------- base ----------

@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    ensure_user(msg.from_user.id, msg.from_user.username or "")
    await msg.answer(
        f"Ку, {msg.from_user.first_name or 'бро'}. Я шкаф-бот.\nКинь фото шмота — разберу по полкам.",
        reply_markup=MAIN_KB,
    )
    await msg.answer(HELP_TEXT)


@dp.message(Command("help"))
async def cmd_help(msg: types.Message):
    await msg.answer(HELP_TEXT, reply_markup=MAIN_KB)


@dp.message(Command("cancel"))
async def cmd_cancel(msg: types.Message, state: FSMContext):
    await state.clear()
    await msg.answer("Отменил. Кидай следующее фото или жми кнопки.", reply_markup=MAIN_KB)


@dp.callback_query(F.data == "noop")
async def cb_noop(cb: types.CallbackQuery):
    await cb.answer()


# ---------- add wizard ----------

@dp.message(Command("add"))
async def cmd_add(msg: types.Message, state: FSMContext):
    await state.set_state(AddItem.waiting_photo)
    await msg.answer("Кидай фото вещи. Можно сразу с подписью-тегами.")


@dp.message(F.text == "➕ Добавить")
async def btn_add(msg: types.Message, state: FSMContext):
    await cmd_add(msg, state)


@dp.message(AddItem.waiting_photo, F.photo)
async def add_photo_step(msg: types.Message, state: FSMContext):
    file_id = msg.photo[-1].file_id
    tags = norm_tags(msg.caption or "")
    await state.update_data(file_id=file_id, tags=tags)
    await state.set_state(AddItem.waiting_category)
    await msg.answer("Что это? Выбери категорию:", reply_markup=kb_categories("addcat"))


@dp.message(AddItem.waiting_photo)
async def add_photo_hint(msg: types.Message):
    await msg.answer("Нужно именно фото. Кинь картинку вещи.")


@dp.callback_query(F.data.startswith("addcat:"))
async def add_cat(cb: types.CallbackQuery, state: FSMContext):
    cat = cb.data.split(":", 1)[1]
    await state.update_data(category=cat)
    await state.set_state(AddItem.waiting_season)
    await cb.message.answer(f"Категория: {label(CATEGORIES, cat)}. Теперь сезон:", reply_markup=kb_seasons("addsea"))
    await cb.answer()


@dp.callback_query(F.data.startswith("addsea:"))
async def add_sea(cb: types.CallbackQuery, state: FSMContext):
    sea = cb.data.split(":", 1)[1]
    await state.update_data(season=sea)
    await state.set_state(AddItem.waiting_color)
    await cb.message.answer(f"Сезон: {label(SEASONS, sea)}. Теперь цвет:", reply_markup=kb_colors("addcol"))
    await cb.answer()


@dp.callback_query(F.data.startswith("addcol:"))
async def add_col(cb: types.CallbackQuery, state: FSMContext):
    col = cb.data.split(":", 1)[1]
    await state.update_data(color=col)
    await state.set_state(AddItem.waiting_style)
    await cb.message.answer(f"Цвет: {label(COLORS, col)}. Стиль вещи:", reply_markup=kb_styles("addsty"))
    await cb.answer()


@dp.callback_query(F.data.startswith("addsty:"))
async def add_sty(cb: types.CallbackQuery, state: FSMContext):
    sty = cb.data.split(":", 1)[1]
    data = await state.get_data()
    await state.update_data(style=sty)
    if data.get("tags"):
        # сразу сохраняем
        await finish_add(cb.message, state, cb.from_user.id)
        await cb.answer()
        return
    await state.set_state(AddItem.waiting_tags)
    await cb.message.answer("Теги через запятую (пример: белая рубашка, офис, хлопок). Или отправь — пропущу.")
    await cb.answer()


@dp.message(AddItem.waiting_tags)
async def add_tags(msg: types.Message, state: FSMContext):
    txt = "" if msg.text.strip() == "—" else msg.text
    await state.update_data(tags=norm_tags(txt))
    await finish_add(msg, state, msg.from_user.id)


async def finish_add(msg: types.Message, state: FSMContext, user_id: int):
    data = await state.get_data()
    ensure_user(user_id)
    item_id = item_add(
        user_id,
        data["file_id"],
        data.get("category", "top"),
        data.get("season", "all"),
        data.get("color", "multi"),
        data.get("style", "casual"),
        data.get("tags", ""),
    )
    await state.clear()
    await msg.answer(f"Сохранил #{item_id}: {data.get('category')}/{data.get('color')}. Кидай следующее фото.", reply_markup=MAIN_KB)


# быстрый путь: фото вне мастера — сразу сохраняем с дефолтами и просим докрутить
@dp.message(F.photo)
async def quick_add(msg: types.Message, state: FSMContext):
    cur = await state.get_state()
    if cur is not None:
        return
    ensure_user(msg.from_user.id, msg.from_user.username or "")
    file_id = msg.photo[-1].file_id
    tags = norm_tags(msg.caption or "")
    # эвристика категории по тегам
    cat = "top"
    low = tags.lower()
    if any(w in low for w in ["кроссов", "ботин", "туфл", "кед", "обувь"]):
        cat = "shoes"
    elif any(w in low for w in ["джинс", "брюк", "штаны", "юбк", "шорт"]):
        cat = "bottom"
    elif any(w in low for w in ["куртк", "пальто", "пуховик", "парк"]):
        cat = "outer"
    elif any(w in low for w in ["платье", "комбез"]):
        cat = "dress"
    elif any(w in low for w in ["шапк", "шарф", "ремень", "сумк", "очк", "часы"]):
        cat = "access"
    item_id = item_add(msg.from_user.id, file_id, cat, "all", "multi", "casual", tags)
    b = InlineKeyboardBuilder()
    b.button(text="Уточнить категорию/цвет", callback_data=f"edit:{item_id}")
    b.button(text="⭐ В избранное", callback_data=f"fav:{item_id}")
    b.adjust(1)
    await msg.answer(f"Быстро сохранил #{item_id} как {label(CATEGORIES, cat)}. Теги: {tags or '—'}", reply_markup=b.as_markup())


@dp.callback_query(F.data.startswith("edit:"))
async def cb_edit_start(cb: types.CallbackQuery, state: FSMContext):
    item_id = int(cb.data.split(":")[1])
    it = item_get(item_id, cb.from_user.id)
    if not it:
        await cb.answer("Не нашёл", show_alert=True)
        return
    await state.update_data(edit_id=item_id, file_id=it["file_id"], tags=it["tags"])
    await state.set_state(AddItem.waiting_category)
    # удалим старую запись, пересоздадим уточнённую
    item_delete(item_id, cb.from_user.id)
    await cb.message.answer("Ок, уточняем. Что это?", reply_markup=kb_categories("addcat"))
    await cb.answer()

# ---------- wardrobe ----------

async def send_wardrobe_page(msg_or_cb, user_id: int, page: int, filt: dict):
    rows, total = items_list(user_id, category=filt.get("cat"), season=filt.get("season"),
                             fav_only=filt.get("fav", False), query=filt.get("q", ""), limit=500)
    pager_cache[user_id] = [r["id"] for r in rows]
    total_pages = max((len(rows) + PAGE_SIZE - 1) // PAGE_SIZE, 1)
    page = max(0, min(page, total_pages - 1))
    chunk = rows[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
    text = f"Шкаф: {total} вещей"
    if filt.get("cat"):
        text += f" • {label(CATEGORIES, filt['cat'])}"
    if filt.get("fav"):
        text += " • ⭐"
    if filt.get("q"):
        text += f" • «{filt['q']}»"
    kb = kb_pager(page, total_pages, "pg")
    if isinstance(msg_or_cb, types.CallbackQuery):
        await msg_or_cb.message.answer(f"{text} (стр {page+1}/{total_pages}):")
        for it in chunk:
            await msg_or_cb.message.answer_photo(it["file_id"], caption=item_caption(it),
                                                 reply_markup=kb_item_card(it["id"], it["fav"]))
        await msg_or_cb.message.answer("Листай:", reply_markup=kb)
        await msg_or_cb.answer()
    else:
        await msg_or_cb.answer(f"{text} (стр {page+1}/{total_pages}):")
        for it in chunk:
            await msg_or_cb.answer_photo(it["file_id"], caption=item_caption(it),
                                         reply_markup=kb_item_card(it["id"], it["fav"]))
        await msg_or_cb.answer("Листай:", reply_markup=kb)


@dp.message(Command("wardrobe"))
async def cmd_wardrobe(msg: types.Message):
    ensure_user(msg.from_user.id)
    await send_wardrobe_page(msg, msg.from_user.id, 0, {})


@dp.message(F.text == "👕 Шкаф")
async def btn_wardrobe(msg: types.Message):
    await cmd_wardrobe(msg)


@dp.callback_query(F.data.startswith("pg:"))
async def cb_page(cb: types.CallbackQuery):
    page = int(cb.data.split(":")[1])
    # фильтры не храним строго — показываем всё
    await send_wardrobe_page(cb, cb.from_user.id, page, {})


@dp.callback_query(F.data.startswith("fav:"))
async def cb_fav(cb: types.CallbackQuery):
    item_id = int(cb.data.split(":")[1])
    nv = item_toggle_fav(item_id, cb.from_user.id)
    if nv is None:
        await cb.answer("Нет такой вещи", show_alert=True)
        return
    await cb.answer("В избранном ⭐" if nv else "Убрал из избранного")


@dp.callback_query(F.data.startswith("del:"))
async def cb_del(cb: types.CallbackQuery):
    item_id = int(cb.data.split(":")[1])
    ok = item_delete(item_id, cb.from_user.id)
    await cb.answer("Удалил" if ok else "Не нашёл")
    if ok:
        await cb.message.delete()


@dp.message(Command("favs"))
async def cmd_favs(msg: types.Message):
    await send_wardrobe_page(msg, msg.from_user.id, 0, {"fav": True})


@dp.message(F.text == "⭐ Избранное")
async def btn_favs(msg: types.Message):
    await cmd_favs(msg)

# ---------- search ----------

@dp.message(Command("search"))
async def cmd_search(msg: types.Message, state: FSMContext):
    await state.set_state(SearchState.waiting_query)
    await msg.answer("Что ищем? Напиши слово: джинсы, белый, офис...")


@dp.message(F.text == "🔍 Поиск")
async def btn_search(msg: types.Message, state: FSMContext):
    await cmd_search(msg, state)


@dp.message(SearchState.waiting_query)
async def search_go(msg: types.Message, state: FSMContext):
    q = norm_tags(msg.text)
    await state.clear()
    await send_wardrobe_page(msg, msg.from_user.id, 0, {"q": q})

# ---------- looks ----------

async def deliver_look(target: types.Message | types.CallbackQuery, user_id: int, items: List[dict], style: str):
    if not items:
        txt = "Шкаф пуст или мало вещей. Кинь 3-5 фото шмота и жми 🎲."
        if isinstance(target, types.CallbackQuery):
            await target.message.answer(txt)
            await target.answer()
        else:
            await target.answer(txt)
        return
    ids = [i["id"] for i in items]
    lid = look_save(user_id, ids, style)
    header = f"Лук #{lid} • {label(STYLES, style)}:"
    if isinstance(target, types.CallbackQuery):
        msg = target.message
        await msg.answer(header)
        for it in items:
            await msg.answer_photo(it["file_id"], caption=item_caption(it))
        await msg.answer("Оцени лук:", reply_markup=kb_look_actions(lid))
        await target.answer()
    else:
        await target.answer(header)
        for it in items:
            await target.answer_photo(it["file_id"], caption=item_caption(it))
        await target.answer("Оцени лук:", reply_markup=kb_look_actions(lid))


@dp.message(Command("look"))
async def cmd_look(msg: types.Message):
    u = ensure_user(msg.from_user.id)
    items = build_random_look(msg.from_user.id, u["season"])
    await deliver_look(msg, msg.from_user.id, items, u["style"])


@dp.message(F.text == "🎲 Лук")
async def btn_look(msg: types.Message):
    await cmd_look(msg)


@dp.message(Command("smart"))
async def cmd_smart(msg: types.Message):
    u = ensure_user(msg.from_user.id)
    await msg.answer("Какой стиль собираем?", reply_markup=kb_styles("smartpick"))


@dp.message(F.text == "🧠 Умный лук")
async def btn_smart(msg: types.Message):
    await cmd_smart(msg)


@dp.callback_query(F.data.startswith("smartpick:"))
async def cb_smartpick(cb: types.CallbackQuery):
    style = cb.data.split(":")[1]
    u = ensure_user(cb.from_user.id)
    items = build_smart_look(cb.from_user.id, style, u["season"])
    await deliver_look(cb, cb.from_user.id, items, style)


@dp.callback_query(F.data == "look:again")
async def cb_again(cb: types.CallbackQuery):
    u = ensure_user(cb.from_user.id)
    items = build_random_look(cb.from_user.id, u["season"])
    await deliver_look(cb, cb.from_user.id, items, u["style"])


@dp.callback_query(F.data == "look:smart")
async def cb_smart_again(cb: types.CallbackQuery):
    u = ensure_user(cb.from_user.id)
    items = build_smart_look(cb.from_user.id, u["style"], u["season"])
    await deliver_look(cb, cb.from_user.id, items, u["style"])


@dp.callback_query(F.data.startswith("rate:"))
async def cb_rate(cb: types.CallbackQuery):
    _, lid, val = cb.data.split(":")
    look_rate(int(lid), cb.from_user.id, int(val))
    await cb.answer("Записал 👍" if val == "5" else "Понял, в следующий раз иначе 👌")


@dp.message(Command("history"))
async def cmd_history(msg: types.Message):
    rows = looks_history(msg.from_user.id, 8)
    if not rows:
        await msg.answer("Луков ещё не было. Жми 🎲.")
        return
    await msg.answer(f"Последние {len(rows)} луков:")
    for r in rows:
        try:
            ids = json.loads(r["item_ids"])
        except Exception:
            ids = []
        items = items_by_ids(msg.from_user.id, ids)
        txt = f"#{r['id']} • {r['created_at'][:16]} • {label(STYLES, r['style'])} • {'👍' if r['rating']==5 else ('👎' if r['rating']==1 else '—')}\n"
        txt += ", ".join([f"#{i['id']} {i['category']}" for i in items]) or "вещи удалены"
        await msg.answer(txt)

# ---------- profile / stats ----------

@dp.message(Command("profile"))
async def cmd_profile(msg: types.Message):
    u = ensure_user(msg.from_user.id)
    await msg.answer(
        f"Профиль:\nСтиль: {label(STYLES, u['style'])}\nСезон: {label(SEASONS, u['season'])}\nЛуков собрано: {u['looks_count']}",
        reply_markup=kb_profile(),
    )


@dp.message(F.text == "👤 Профиль")
async def btn_profile(msg: types.Message):
    await cmd_profile(msg)


@dp.callback_query(F.data == "prof:style")
async def cb_prof_style(cb: types.CallbackQuery):
    await cb.message.answer("Выбери базовый стиль:", reply_markup=kb_styles("setsty"))
    await cb.answer()


@dp.callback_query(F.data == "prof:season")
async def cb_prof_season(cb: types.CallbackQuery):
    await cb.message.answer("Выбери сезон:", reply_markup=kb_seasons("setsea"))
    await cb.answer()


@dp.callback_query(F.data.startswith("setsty:"))
async def cb_set_sty(cb: types.CallbackQuery):
    update_profile(cb.from_user.id, style=cb.data.split(":")[1])
    await cb.message.answer(f"Стиль обновил: {label(STYLES, cb.data.split(':')[1])}")
    await cb.answer()


@dp.callback_query(F.data.startswith("setsea:"))
async def cb_set_sea(cb: types.CallbackQuery):
    update_profile(cb.from_user.id, season=cb.data.split(":")[1])
    await cb.message.answer(f"Сезон обновил: {label(SEASONS, cb.data.split(':')[1])}")
    await cb.answer()


@dp.callback_query(F.data == "prof:stats")
async def cb_prof_stats(cb: types.CallbackQuery):
    await send_stats(cb.message, cb.from_user.id)
    await cb.answer()


def format_stats(s: dict) -> str:
    lines = [f"Всего вещей: {s['total']}", f"В избранном: {s['favs']}", f"Луков: {s['looks']}", "", "По категориям:"]
    for code, _name in CATEGORIES:
        if code in s["by_cat"]:
            lines.append(f"— {label(CATEGORIES, code)}: {s['by_cat'][code]}")
    lines.append("")
    lines.append("По цветам:")
    for code, _name in COLORS:
        if code in s["by_color"]:
            lines.append(f"— {label(COLORS, code)}: {s['by_color'][code]}")
    if s["top_tags"]:
        lines.append("")
        lines.append("Топ теги: " + ", ".join([f"{k}({v})" for k, v in s["top_tags"]]))
    # советы
    lines.append("")
    if s["total"] < 5:
        lines.append("Совет: докинь ещё 5-7 вещей, луки будут разнообразнее.")
    elif len(s["by_cat"]) < 3:
        lines.append("Совет: перекос в одну категорию, добавь обувь/низ для комбо.")
    else:
        lines.append("Шкаф в норме. Жми 🧠 для умного лука.")
    return "\n".join(lines)


async def send_stats(msg: types.Message, user_id: int):
    ensure_user(user_id)
    await msg.answer(format_stats(stats_for(user_id)))


@dp.message(Command("stats"))
async def cmd_stats(msg: types.Message):
    await send_stats(msg, msg.from_user.id)


@dp.message(F.text == "📊 Стата")
async def btn_stats(msg: types.Message):
    await cmd_stats(msg)

# ---------- export / clear ----------

@dp.message(Command("export"))
async def cmd_export(msg: types.Message):
    data = export_json(msg.from_user.id)
    buf = io.BytesIO(data.encode("utf-8"))
    buf.name = f"wardrobe_{msg.from_user.id}.json"
    await msg.answer_document(types.BufferedInputFile(buf.getvalue(), filename=buf.name), caption="Выгрузка шкафа")


@dp.message(Command("clear"))
async def cmd_clear(msg: types.Message):
    b = InlineKeyboardBuilder()
    b.button(text="Да, снести всё", callback_data="wipe:yes")
    b.button(text="Нет", callback_data="wipe:no")
    b.adjust(2)
    await msg.answer("Точно очистить шкаф, луки и историю?", reply_markup=b.as_markup())


@dp.callback_query(F.data.startswith("wipe:"))
async def cb_wipe(cb: types.CallbackQuery):
    if cb.data.endswith("yes"):
        wipe_user(cb.from_user.id)
        await cb.message.answer("Снёс. Начинаем с нуля — кидай фото.")
    else:
        await cb.message.answer("Оставил как было.")
    await cb.answer()

# ---------- fallback ----------

@dp.message()
async def fallback(msg: types.Message):
    if msg.text and msg.text.startswith("/"):
        await msg.answer("Не знаю такую команду. /help — список.")
        return
    await msg.answer("Кинь фото шмота — сохраню. Или жми 🎲 для лука.", reply_markup=MAIN_KB)

# ---------- errors ----------

@dp.error()
async def on_error(event, exception):
    log.exception("update failed: %s", exception)
    return True

# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

async def main():
    if BOT_TOKEN.startswith("ВСТАВЬ"):
        print("!!! Нет токена. set BOT_TOKEN=xxx")
        return
    db_init()
    log.info("starting polling...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("stop")

