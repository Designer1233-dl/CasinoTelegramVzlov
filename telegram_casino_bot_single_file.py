# Telegram Casino Bot (Single File)
# Features:
# - Telegram dice games for 1, 2 and 3 dice
# - Balance, wager, deposits via CryptoBot and admin-approved withdrawals
# - Admin dashboard with casino stats, reserve controls and gift checks
# - Premium emoji placeholders in one dictionary
# - SQLite database with lightweight migrations

import asyncio
import html
import logging
import os
import secrets
import sqlite3
from datetime import datetime

from aiocryptopay import AioCryptoPay, Networks
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)


BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CRYPTOBOT_TOKEN = os.getenv("CRYPTOBOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DB_NAME = "casino.db"
MIN_BET = 0.1

logging.basicConfig(level=logging.INFO)

bot = Bot(BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
crypto = AioCryptoPay(token=CRYPTOBOT_TOKEN, network=Networks.MAIN_NET)


# Put premium emoji IDs here after Telegram gives them to you.
# Example: "dice": "5368324170671202286"
PREMIUM_EMOJI_IDS = {
    "casino": "5291914649481007565",
    "dice": "5890885351152553528",
    "money": "5197434882321567830",
    "deposit": "5258336354642697821",
    "withdraw": "5260379144167890225",
    "wallet": "5260379144167890225",
    "profile": "5258073068852485953",
    "admin": "5442939099906325301",
    "win": "5260616239247563540",
    "lose": "5260258807774218256",
    "reserve": "5361543877599724417",
    "gift": "5330312778093704176",
    "chart": "5357593469760059395",
    "back": "5258420634785947640",
    # Пока отдельный ID поддержки не прислан, используется админский значок.
    "support": "5442939099906325301",
    "broadcast": "5442939099906325301",
    "referral": "5258073068852485953",
}

BUTTON_FALLBACKS = {
    "casino": "🎰",
    "dice": "🎲",
    "money": "💵",
    "deposit": "💳",
    "withdraw": "💸",
    "wallet": "💼",
    "profile": "👤",
    "admin": "🛠",
    "win": "✅",
    "lose": "❌",
    "reserve": "🏦",
    "gift": "🎁",
    "chart": "📊",
    "back": "⬅️",
    "support": "🆘",
    "broadcast": "📣",
    "referral": "👥",
}

def pe(key: str, fallback: str) -> str:
    emoji_id = PREMIUM_EMOJI_IDS.get(key)
    if emoji_id:
        return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'
    return fallback


def button_text(text: str, icon_key: str | None = None, fallback: str | None = None) -> str:
    # When Telegram supports custom button icons, the premium icon is rendered
    # by icon_custom_emoji_id. Avoid adding a second ordinary emoji to the label.
    if icon_key and PREMIUM_EMOJI_IDS.get(icon_key):
        return text
    icon = fallback or BUTTON_FALLBACKS.get(icon_key)
    return f"{icon} {text}" if icon else text


def ib(text: str, icon_key: str | None = None, fallback: str | None = None, **kwargs):
    fields = getattr(InlineKeyboardButton, "model_fields", None) or getattr(
        InlineKeyboardButton, "__fields__", {}
    )
    if icon_key and PREMIUM_EMOJI_IDS.get(icon_key) and "icon_custom_emoji_id" in fields:
        kwargs["icon_custom_emoji_id"] = PREMIUM_EMOJI_IDS[icon_key]
    return InlineKeyboardButton(text=button_text(text, icon_key, fallback), **kwargs)


def kb(text: str, icon_key: str | None = None, fallback: str | None = None, **kwargs):
    fields = getattr(KeyboardButton, "model_fields", None) or getattr(KeyboardButton, "__fields__", {})
    if icon_key and PREMIUM_EMOJI_IDS.get(icon_key) and "icon_custom_emoji_id" in fields:
        kwargs["icon_custom_emoji_id"] = PREMIUM_EMOJI_IDS[icon_key]
    return KeyboardButton(text=button_text(text, icon_key, fallback), **kwargs)


async def callback_notice(callback: types.CallbackQuery, key: str, text: str):
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            f"{pe(key, BUTTON_FALLBACKS.get(key, 'ℹ️'))} {text}",
            parse_mode="HTML",
        )


def db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def has_column(cur, table: str, column: str) -> bool:
    cur.execute(f"PRAGMA table_info({table})")
    return any(row["name"] == column for row in cur.fetchall())


def init_db():
    conn = db()
    cur = conn.cursor()

    cur.execute(
        """CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            balance REAL DEFAULT 0,
            wager REAL DEFAULT 0,
            wager_progress REAL DEFAULT 0,
            username TEXT,
            current_bet REAL DEFAULT 0.5,
            referred_by INTEGER,
            referral_earnings REAL DEFAULT 0
        )"""
    )
    if not has_column(cur, "users", "current_bet"):
        cur.execute("ALTER TABLE users ADD COLUMN current_bet REAL DEFAULT 0.5")
    if not has_column(cur, "users", "referred_by"):
        cur.execute("ALTER TABLE users ADD COLUMN referred_by INTEGER")
    if not has_column(cur, "users", "referral_earnings"):
        cur.execute("ALTER TABLE users ADD COLUMN referral_earnings REAL DEFAULT 0")

    cur.execute(
        """CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            wallet TEXT,
            status TEXT DEFAULT 'pending'
        )"""
    )
    if not has_column(cur, "withdrawals", "created_at"):
        cur.execute("ALTER TABLE withdrawals ADD COLUMN created_at TEXT")
    if not has_column(cur, "withdrawals", "processed_at"):
        cur.execute("ALTER TABLE withdrawals ADD COLUMN processed_at TEXT")

    cur.execute(
        """CREATE TABLE IF NOT EXISTS checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT,
            amount REAL,
            activations INTEGER,
            used INTEGER DEFAULT 0,
            wager REAL DEFAULT 0,
            creator_id INTEGER,
            referral_only INTEGER DEFAULT 0
        )"""
    )
    if not has_column(cur, "checks", "creator_id"):
        cur.execute("ALTER TABLE checks ADD COLUMN creator_id INTEGER")
    if not has_column(cur, "checks", "referral_only"):
        cur.execute("ALTER TABLE checks ADD COLUMN referral_only INTEGER DEFAULT 0")

    cur.execute(
        """CREATE TABLE IF NOT EXISTS check_activations (
            user_id INTEGER,
            check_id INTEGER
        )"""
    )

    cur.execute(
        """CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )"""
    )

    cur.execute(
        """CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            kind TEXT NOT NULL,
            amount REAL NOT NULL,
            meta TEXT,
            created_at TEXT NOT NULL
        )"""
    )

    cur.execute(
        """CREATE TABLE IF NOT EXISTS game_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            game_key TEXT NOT NULL,
            bet REAL NOT NULL,
            payout REAL NOT NULL,
            profit REAL NOT NULL,
            rolls TEXT NOT NULL,
            won INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )"""
    )

    cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('reserve', '0')")
    cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('support_url', '')")

    conn.commit()
    conn.close()


init_db()


def now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def money(value: float) -> str:
    return f"${value:.2f}"


def display_name(user: types.User) -> str:
    if user.username:
        return f"@{html.escape(user.username)}"
    return html.escape(user.full_name or str(user.id))


def is_admin(user_id: int) -> bool:
    return bool(ADMIN_ID) and user_id == ADMIN_ID


def parse_positive_float(text: str, minimum: float = 0.01) -> float | None:
    try:
        value = float(text.replace(",", ".").strip())
    except (TypeError, ValueError):
        return None
    if value < minimum:
        return None
    return value


def get_user(user_id: int, username: str | None = None):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    user = cur.fetchone()
    if not user:
        cur.execute("INSERT INTO users (user_id, username) VALUES (?, ?)", (user_id, username))
        conn.commit()
        cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        user = cur.fetchone()
    elif username and user["username"] != username:
        cur.execute("UPDATE users SET username=? WHERE user_id=?", (username, user_id))
        conn.commit()
        cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        user = cur.fetchone()
    conn.close()
    return user


def normalize_username(value: str) -> str:
    username = (value or "").strip()
    if username.startswith("@"):
        username = username[1:]
    return username


def get_user_by_username(username: str):
    username = normalize_username(username)
    if not username:
        return None

    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM users WHERE LOWER(username)=LOWER(?) LIMIT 1",
        (username,),
    )
    user = cur.fetchone()
    conn.close()
    return user


def update_balance(user_id: int, amount: float):
    get_user(user_id)
    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, user_id))
    conn.commit()
    conn.close()


def debit_balance(user_id: int, amount: float) -> bool:
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET balance = balance - ? WHERE user_id=? AND balance >= ?",
        (amount, user_id, amount),
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def add_wager(user_id: int, amount: float):
    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET wager = wager + ? WHERE user_id=?", (amount, user_id))
    conn.commit()
    conn.close()


def update_wager_progress(user_id: int, amount: float):
    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET wager_progress = wager_progress + ? WHERE user_id=?", (amount, user_id))
    conn.commit()
    conn.close()


def set_current_bet(user_id: int, amount: float):
    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET current_bet=? WHERE user_id=?", (amount, user_id))
    conn.commit()
    conn.close()


def attach_referrer(user_id: int, referrer_id: int) -> bool:
    if user_id == referrer_id:
        return False

    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT referred_by FROM users WHERE user_id=?", (user_id,))
    user = cur.fetchone()
    cur.execute("SELECT 1 FROM users WHERE user_id=?", (referrer_id,))
    referrer = cur.fetchone()

    if not user or not referrer or user["referred_by"]:
        conn.close()
        return False

    cur.execute(
        "UPDATE users SET referred_by=? WHERE user_id=? AND referred_by IS NULL",
        (referrer_id, user_id),
    )
    attached = cur.rowcount > 0
    conn.commit()
    conn.close()
    return attached


def referral_stats(user_id: int):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM users WHERE referred_by=?", (user_id,))
    count = cur.fetchone()["c"]
    cur.execute("SELECT COALESCE(referral_earnings, 0) AS e FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return {
        "count": count,
        "earnings": row["e"] if row else 0.0,
    }


def get_referrer_id(user_id: int) -> int | None:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT referred_by FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    conn.close()
    if not row or row["referred_by"] is None:
        return None
    return int(row["referred_by"])


def add_referral_earning(referrer_id: int, amount: float):
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET balance = balance + ?, referral_earnings = referral_earnings + ? WHERE user_id=?",
        (amount, amount, referrer_id),
    )
    conn.commit()
    conn.close()


def record_transaction(kind: str, amount: float, user_id: int | None = None, meta: str = ""):
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO transactions (user_id, kind, amount, meta, created_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, kind, amount, meta, now_iso()),
    )
    conn.commit()
    conn.close()


def record_game(user_id: int, game_key: str, bet: float, payout: float, rolls: list[int], won: bool):
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO game_history
           (user_id, game_key, bet, payout, profit, rolls, won, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, game_key, bet, payout, bet - payout, ",".join(map(str, rolls)), int(won), now_iso()),
    )
    conn.commit()
    conn.close()


async def pay_referral_commission(referee: types.User, game_key: str, lost_amount: float):
    referrer_id = get_referrer_id(referee.id)
    if not referrer_id or referrer_id == referee.id:
        return

    commission = round(lost_amount * 0.10, 2)
    if commission <= 0:
        return

    add_referral_earning(referrer_id, commission)
    record_transaction(
        "referral_commission",
        commission,
        referrer_id,
        f"referee:{referee.id};game:{game_key}",
    )

    try:
        await bot.send_message(
            referrer_id,
            f"{pe('referral', '👥')} <b>Реферальное начисление</b>\n\n"
            f"{pe('profile', '👤')} Реферал: <b>{display_name(referee)}</b>\n"
            f"{pe('lose', '💀')} Проигранная ставка: <b>{money(lost_amount)}</b>\n"
            f"{pe('money', '💵')} Вам начислено 10%: <b>{money(commission)}</b>",
            parse_mode="HTML",
        )
    except Exception as error:
        logging.info("Referral notification failed for %s: %s", referrer_id, error)


def get_setting_float(key: str, default: float = 0.0) -> float:
    value = get_setting(key)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def get_setting(key: str, default: str = "") -> str:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = cur.fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key: str, value: str):
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


def set_setting_float(key: str, value: float):
    set_setting(key, str(value))


def change_reserve(delta: float):
    new_value = max(0.0, get_setting_float("reserve") + delta)
    set_setting_float("reserve", new_value)
    record_transaction("reserve_add" if delta >= 0 else "reserve_remove", abs(delta), ADMIN_ID)
    return new_value


def casino_stats():
    conn = db()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) AS c, COALESCE(SUM(balance), 0) AS balance FROM users")
    users = cur.fetchone()
    cur.execute("SELECT COALESCE(SUM(amount), 0) AS s FROM transactions WHERE kind='deposit'")
    deposits = cur.fetchone()["s"]
    cur.execute("SELECT COALESCE(SUM(amount), 0) AS s FROM withdrawals WHERE status='approved'")
    approved_withdraws = cur.fetchone()["s"]
    cur.execute("SELECT COALESCE(SUM(amount), 0) AS s FROM withdrawals WHERE status='pending'")
    pending_withdraws = cur.fetchone()["s"]
    cur.execute(
        "SELECT COALESCE(SUM(bet), 0) AS bet, COALESCE(SUM(payout), 0) AS payout, "
        "COALESCE(SUM(profit), 0) AS profit, COUNT(*) AS games FROM game_history"
    )
    games = cur.fetchone()
    cur.execute(
        "SELECT COUNT(*) AS c, COALESCE(SUM(amount), 0) AS total "
        "FROM transactions WHERE kind='referral_commission'"
    )
    referrals = cur.fetchone()
    conn.close()

    return {
        "users": users["c"],
        "balances": users["balance"],
        "deposits": deposits,
        "approved_withdraws": approved_withdraws,
        "pending_withdraws": pending_withdraws,
        "bets": games["bet"],
        "payouts": games["payout"],
        "game_profit": games["profit"],
        "games": games["games"],
        "referral_commissions": referrals["total"],
        "referral_payments": referrals["c"],
        "reserve": get_setting_float("reserve"),
    }


def game_def(key: str, title: str, dice_count: int, multiplier: float, description: str):
    return {
        "key": key,
        "title": title,
        "dice_count": dice_count,
        "multiplier": multiplier,
        "description": description,
    }


GAMES = {
    "d1_even": game_def("d1_even", "Чет", 1, 2.0, "кубик должен выпасть четным"),
    "d1_odd": game_def("d1_odd", "Нечет", 1, 2.0, "кубик должен выпасть нечетным"),
    "d1_less": game_def("d1_less", "Меньше", 1, 2.0, "кубик должен быть 1, 2 или 3"),
    "d1_more": game_def("d1_more", "Больше", 1, 2.0, "кубик должен быть 4, 5 или 6"),
    "d1_exact_1": game_def("d1_exact_1", "Точно 1", 1, 6.0, "кубик должен выпасть 1"),
    "d1_exact_2": game_def("d1_exact_2", "Точно 2", 1, 6.0, "кубик должен выпасть 2"),
    "d1_exact_3": game_def("d1_exact_3", "Точно 3", 1, 6.0, "кубик должен выпасть 3"),
    "d1_exact_4": game_def("d1_exact_4", "Точно 4", 1, 6.0, "кубик должен выпасть 4"),
    "d1_exact_5": game_def("d1_exact_5", "Точно 5", 1, 6.0, "кубик должен выпасть 5"),
    "d1_exact_6": game_def("d1_exact_6", "Точно 6", 1, 6.0, "кубик должен выпасть 6"),
    "d2_even": game_def("d2_even", "Оба четных", 2, 4.0, "оба кубика должны быть четными"),
    "d2_odd": game_def("d2_odd", "Оба нечет", 2, 4.0, "оба кубика должны быть нечетными"),
    "d2_less": game_def("d2_less", "Оба меньше 4", 2, 3.77, "оба кубика должны быть меньше 4"),
    "d2_more": game_def("d2_more", "Оба больше 3", 2, 3.77, "оба кубика должны быть больше 3"),
    "d2_first_gt": game_def("d2_first_gt", "Куб 1 > 2", 2, 2.4, "первый кубик должен быть больше второго"),
    "d2_first_lt": game_def("d2_first_lt", "Куб 1 < 2", 2, 2.4, "первый кубик должен быть меньше второго"),
    "d2_double": game_def("d2_double", "Любой дубль", 2, 6.0, "оба кубика должны быть одинаковыми"),
    "d2_product_18": game_def("d2_product_18", "Произвед > 18", 2, 3.9, "произведение двух кубиков должно быть больше 18"),
    "d3_even": game_def("d3_even", "Три четных", 3, 8.0, "все три кубика должны быть четными"),
    "d3_odd": game_def("d3_odd", "Три нечет", 3, 8.0, "все три кубика должны быть нечетными"),
    "d3_less": game_def("d3_less", "Три меньше", 3, 8.0, "все три кубика должны быть меньше 4"),
    "d3_more": game_def("d3_more", "Три больше", 3, 8.0, "все три кубика должны быть больше 3"),
    "d3_down": game_def("d3_down", "Убывание", 3, 10.8, "кубики должны идти строго по убыванию"),
    "d3_up": game_def("d3_up", "Возрастание", 3, 10.8, "кубики должны идти строго по возрастанию"),
    "d3_any_triple": game_def("d3_any_triple", "Любой трипл", 3, 36.0, "все три кубика должны совпасть"),
    "d3_duel": game_def("d3_duel", "Дуэль", 3, 3.92, "должна выпасть ровно одна пара"),
    "d3_straight": game_def("d3_straight", "Стрит", 3, 9.0, "три кубика должны собрать последовательность"),
    "d3_product_108": game_def("d3_product_108", "Произвед > 108", 3, 12.7, "произведение трех кубиков должно быть больше 108"),
}

for number in range(1, 7):
    GAMES[f"d2_exact_{number}"] = game_def(
        f"d2_exact_{number}",
        f"Дубль {number}",
        2,
        36.0,
        f"оба кубика должны выпасть {number}",
    )
    GAMES[f"d3_exact_{number}"] = game_def(
        f"d3_exact_{number}",
        f"Трипл {number}",
        3,
        216.0,
        f"все три кубика должны выпасть {number}",
    )

SUM_MULTIPLIERS_3D = {
    3: 54.0,
    4: 36.0,
    5: 27.0,
    6: 18.0,
    7: 13.5,
    8: 10.8,
    9: 9.0,
    10: 8.0,
    11: 8.0,
    12: 9.0,
    13: 10.8,
    14: 13.5,
    15: 18.0,
    16: 27.0,
    17: 36.0,
    18: 54.0,
}

for total, multiplier in SUM_MULTIPLIERS_3D.items():
    GAMES[f"d3_sum_{total}"] = game_def(
        f"d3_sum_{total}",
        f"Сумма {total}",
        3,
        multiplier,
        f"сумма трех кубиков должна быть {total}",
    )


def is_win(game_key: str, rolls: list[int]) -> bool:
    if game_key == "d1_even":
        return rolls[0] % 2 == 0
    if game_key == "d1_odd":
        return rolls[0] % 2 == 1
    if game_key == "d1_less":
        return rolls[0] < 4
    if game_key == "d1_more":
        return rolls[0] > 3
    if game_key.startswith("d1_exact_"):
        return rolls[0] == int(game_key.rsplit("_", 1)[1])

    if game_key == "d2_even":
        return all(value % 2 == 0 for value in rolls)
    if game_key == "d2_odd":
        return all(value % 2 == 1 for value in rolls)
    if game_key == "d2_less":
        return all(value < 4 for value in rolls)
    if game_key == "d2_more":
        return all(value > 3 for value in rolls)
    if game_key == "d2_first_gt":
        return rolls[0] > rolls[1]
    if game_key == "d2_first_lt":
        return rolls[0] < rolls[1]
    if game_key == "d2_double":
        return rolls[0] == rolls[1]
    if game_key == "d2_product_18":
        return rolls[0] * rolls[1] > 18
    if game_key.startswith("d2_exact_"):
        target = int(game_key.rsplit("_", 1)[1])
        return rolls == [target, target]

    if game_key == "d3_even":
        return all(value % 2 == 0 for value in rolls)
    if game_key == "d3_odd":
        return all(value % 2 == 1 for value in rolls)
    if game_key == "d3_less":
        return all(value < 4 for value in rolls)
    if game_key == "d3_more":
        return all(value > 3 for value in rolls)
    if game_key == "d3_down":
        return rolls[0] > rolls[1] > rolls[2]
    if game_key == "d3_up":
        return rolls[0] < rolls[1] < rolls[2]
    if game_key == "d3_any_triple":
        return len(set(rolls)) == 1
    if game_key == "d3_duel":
        return len(set(rolls)) == 2
    if game_key == "d3_straight":
        ordered = sorted(rolls)
        return ordered in ([1, 2, 3], [2, 3, 4], [3, 4, 5], [4, 5, 6])
    if game_key == "d3_product_108":
        return rolls[0] * rolls[1] * rolls[2] > 108
    if game_key.startswith("d3_exact_"):
        target = int(game_key.rsplit("_", 1)[1])
        return rolls == [target, target, target]
    if game_key.startswith("d3_sum_"):
        total = int(game_key.rsplit("_", 1)[1])
        return sum(rolls) == total

    return False


def bottom_menu(user_id: int | None = None):
    keyboard = [
        [
            kb("Баланс", "wallet"),
            kb("Играть", "dice"),
            kb("Меню", "casino", fallback="☰"),
        ]
    ]
    if user_id is not None and is_admin(user_id):
        keyboard.append([kb("Админ-панель", "admin")])
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
    )


def main_menu(user_id: int | None = None):
    rows = [
        [
            ib("Играть", "dice", callback_data="games"),
            ib("Профиль", "profile", callback_data="profile"),
        ],
        [
            ib("Резерв", "reserve", callback_data="reserve_view"),
            ib("Рефералы", "referral", callback_data="referral_info"),
        ],
    ]
    support_url = get_setting("support_url").strip()
    if support_url:
        rows[-1].append(ib("Поддержка", "support", url=support_url))
    if user_id is not None and is_admin(user_id):
        rows.append([ib("Админ-панель", "admin", callback_data="admin_open")])
    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


def profile_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                ib("Пополнить", "deposit", callback_data="deposit"),
                ib("Вывести", "withdraw", callback_data="withdraw"),
            ],
            [ib("Рефералы", "referral", callback_data="referral_info")],
            [ib("Назад", "back", callback_data="back")],
        ]
    )


def referral_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [ib("Создать чек для рефов", "gift", callback_data="referral_createcheck")],
            [ib("Меню", "casino", callback_data="back")],
        ]
    )


def check_scope_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                ib("Только мои рефералы", "referral", callback_data="check_scope_referrals"),
                ib("Для всех игроков", "gift", callback_data="check_scope_all"),
            ],
        ]
    )


def games_menu(dice_count: int):
    one_active = "1 Бросок"
    two_active = "2 Броска"
    three_active = "3 Броска"

    rows = [
        [
            ib(one_active, "dice", callback_data="dice_1"),
            ib(two_active, "dice", callback_data="dice_2"),
            ib(three_active, "dice", callback_data="dice_3"),
        ],
        [
            ib("Изменить ставку", "money", callback_data=f"set_bet_{dice_count}"),
            ib("Баланс", "wallet", callback_data="profile"),
        ],
    ]

    if dice_count == 1:
        rows += [
            [
                ib("Чет (x2)", "dice", callback_data="game_d1_even"),
                ib("Нечет (x2)", "dice", callback_data="game_d1_odd"),
            ],
            [
                ib("Меньше (x2)", "dice", callback_data="game_d1_less"),
                ib("Больше (x2)", "dice", callback_data="game_d1_more"),
            ],
            [
                ib("1 (x6)", "dice", callback_data="game_d1_exact_1"),
                ib("2 (x6)", "dice", callback_data="game_d1_exact_2"),
                ib("3 (x6)", "dice", callback_data="game_d1_exact_3"),
            ],
            [
                ib("4 (x6)", "dice", callback_data="game_d1_exact_4"),
                ib("5 (x6)", "dice", callback_data="game_d1_exact_5"),
                ib("6 (x6)", "dice", callback_data="game_d1_exact_6"),
            ],
        ]
    elif dice_count == 2:
        rows += [
            [
                ib("Оба четных (x4)", "dice", callback_data="game_d2_even"),
                ib("Оба нечет (x4)", "dice", callback_data="game_d2_odd"),
            ],
            [
                ib("Оба меньше 4 (x3.77)", "dice", callback_data="game_d2_less"),
                ib("Оба больше 3 (x3.77)", "dice", callback_data="game_d2_more"),
            ],
            [
                ib("Куб 1 > 2 (x2.4)", "dice", callback_data="game_d2_first_gt"),
                ib("Куб 1 < 2 (x2.4)", "dice", callback_data="game_d2_first_lt"),
            ],
            [
                ib("1 (x36)", "dice", callback_data="game_d2_exact_1"),
                ib("2 (x36)", "dice", callback_data="game_d2_exact_2"),
                ib("3 (x36)", "dice", callback_data="game_d2_exact_3"),
            ],
            [
                ib("4 (x36)", "dice", callback_data="game_d2_exact_4"),
                ib("5 (x36)", "dice", callback_data="game_d2_exact_5"),
                ib("6 (x36)", "dice", callback_data="game_d2_exact_6"),
            ],
            [
                ib("Любой дубль (x6)", "dice", callback_data="game_d2_double"),
                ib("Произвед > 18 (x3.9)", "dice", callback_data="game_d2_product_18"),
            ],
        ]
    else:
        rows += [
            [
                ib("Три четных (x8)", "dice", callback_data="game_d3_even"),
                ib("Три нечет (x8)", "dice", callback_data="game_d3_odd"),
            ],
            [
                ib("Три меньше (x8)", "dice", callback_data="game_d3_less"),
                ib("Три больше (x8)", "dice", callback_data="game_d3_more"),
            ],
            [
                ib("Убывание (x10.8)", "dice", callback_data="game_d3_down"),
                ib("Возрастание (x10.8)", "dice", callback_data="game_d3_up"),
            ],
            [
                ib("1 (x216)", "dice", callback_data="game_d3_exact_1"),
                ib("2 (x216)", "dice", callback_data="game_d3_exact_2"),
                ib("3 (x216)", "dice", callback_data="game_d3_exact_3"),
            ],
            [
                ib("4 (x216)", "dice", callback_data="game_d3_exact_4"),
                ib("5 (x216)", "dice", callback_data="game_d3_exact_5"),
                ib("6 (x216)", "dice", callback_data="game_d3_exact_6"),
            ],
            [
                ib("Сумма (до x54)", "dice", callback_data="sum3_menu"),
                ib("Любой трипл (x36)", "dice", callback_data="game_d3_any_triple"),
            ],
            [
                ib("Дуэль (x3.92)", "dice", callback_data="game_d3_duel"),
                ib("Стрит (x9)", "dice", callback_data="game_d3_straight"),
            ],
            [ib("Произвед > 108 (x12.7)", "dice", callback_data="game_d3_product_108")],
        ]

    rows.append([ib("Назад", "back", callback_data="back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def sum3_menu():
    rows = []
    for left, right in [(3, 18), (4, 17), (5, 16), (6, 15), (7, 14), (8, 13), (9, 12), (10, 11)]:
        rows.append(
            [
                ib(f"{left} (x{SUM_MULTIPLIERS_3D[left]:g})", "dice", callback_data=f"game_d3_sum_{left}"),
                ib(f"{right} (x{SUM_MULTIPLIERS_3D[right]:g})", "dice", callback_data=f"game_d3_sum_{right}"),
            ]
        )
    rows.append([ib("К 3 броскам", "back", callback_data="dice_3")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [ib("Статистика", "chart", callback_data="admin_stats")],
            [
                ib("Я пополнил резерв", "reserve", callback_data="admin_reserve_add"),
                ib("Я снял резерв", "withdraw", callback_data="admin_reserve_remove"),
            ],
            [
                ib("Выдать по username", "money", callback_data="admin_balance_add"),
                ib("Снять по username", "withdraw", callback_data="admin_balance_remove"),
            ],
            [
                ib("Создать чек", "gift", callback_data="admin_createcheck"),
                ib("Настроить поддержку", "support", callback_data="admin_support"),
            ],
            [ib("Рассылка", "broadcast", callback_data="admin_broadcast")],
            [ib("Назад", "back", callback_data="back")],
        ]
    )


def result_menu(game_key: str):
    game = GAMES[game_key]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [ib("Повторить ставку", "money", callback_data=f"repeat_{game_key}")],
            [ib("Выбрать другую игру", "dice", callback_data=f"dice_{game['dice_count']}")],
            [ib("Профиль", "profile", callback_data="profile")],
        ]
    )


def game_menu_text(user, dice_count: int) -> str:
    return (
        f"{pe('dice', '🎲')} <b>Выберите исход игры</b>\n\n"
        f"{pe('money', '💵')} Ставка: <b>{money(user['current_bet'])}</b>\n"
        f"{pe('wallet', '💼')} Баланс: <b>{money(user['balance'])}</b>\n\n"
        f"<i>Сейчас выбран режим: {dice_count} {'кубик' if dice_count == 1 else 'кубика'}</i>"
    )


def profile_text(user) -> str:
    left = max(0.0, user["wager"] - user["wager_progress"])
    return (
        f"{pe('profile', '👤')} <b>Профиль</b>\n\n"
        f"{pe('wallet', '💼')} Баланс: <b>{money(user['balance'])}</b>\n"
        f"{pe('dice', '🎯')} Вагер: <b>{money(user['wager'])}</b>\n"
        f"{pe('chart', '📈')} Отыгрыш: <b>{money(user['wager_progress'])}/{money(user['wager'])}</b>\n"
        f"{pe('referral', '👥')} Реферальный доход: <b>{money(user['referral_earnings'])}</b>\n"
        f"До вывода осталось: <b>{money(left)}</b>"
    )


async def referral_text(user_id: int) -> str:
    stats = referral_stats(user_id)
    bot_info = await bot.get_me()
    link = f"https://t.me/{bot_info.username}?start=ref_{user_id}"
    return (
        f"{pe('referral', '👥')} <b>Реферальная система</b>\n\n"
        f"{pe('money', '💵')} Вы получаете <b>10%</b> от каждой проигранной ставки вашего реферала.\n"
        f"{pe('profile', '👤')} Приглашено: <b>{stats['count']}</b>\n"
        f"{pe('money', '💵')} Заработано: <b>{money(stats['earnings'])}</b>\n\n"
        f"{pe('gift', '🎁')} Можно создать чек для своих рефералов за счёт своего баланса.\n\n"
        f"Ваша ссылка:\n<code>{link}</code>"
    )


def reserve_text() -> str:
    reserve = get_setting_float("reserve")
    return f"{pe('reserve', '🏦')} <b>Резерв казино</b>\n\nТекущий резерв: <b>{money(reserve)}</b>"


def admin_stats_text() -> str:
    stats = casino_stats()
    sign = "+" if stats["game_profit"] >= 0 else ""
    support_url = get_setting("support_url").strip() or "не задана"
    return (
        f"{pe('admin', '🛠')} <b>Админ-панель</b>\n\n"
        f"{pe('chart', '📊')} Игры: <b>{stats['games']}</b>\n"
        f"Ставки игроков: <b>{money(stats['bets'])}</b>\n"
        f"Выплаты по играм: <b>{money(stats['payouts'])}</b>\n"
        f"Плюс/минус казино по играм: <b>{sign}{money(stats['game_profit'])}</b>\n\n"
        f"{pe('referral', '👥')} Реферальных начислений: <b>{money(stats['referral_commissions'])}</b>\n"
        f"{pe('money', '💳')} Пополнения игроков: <b>{money(stats['deposits'])}</b>\n"
        f"{pe('wallet', '💸')} Подтвержденные выводы: <b>{money(stats['approved_withdraws'])}</b>\n"
        f"Заявки на вывод в ожидании: <b>{money(stats['pending_withdraws'])}</b>\n\n"
        f"{pe('profile', '👥')} Игроков: <b>{stats['users']}</b>\n"
        f"Баланс у игроков всего: <b>{money(stats['balances'])}</b>\n"
        f"{pe('reserve', '🏦')} Резерв: <b>{money(stats['reserve'])}</b>\n"
        f"{pe('support', '🆘')} Поддержка: <code>{html.escape(support_url)}</code>"
    )


class DepositState(StatesGroup):
    amount = State()


class WithdrawState(StatesGroup):
    amount = State()
    wallet = State()


class AdminCheckState(StatesGroup):
    amount = State()
    activations = State()
    wager = State()
    scope = State()


class AdminReserveState(StatesGroup):
    amount = State()
    action = State()


class AdminBalanceState(StatesGroup):
    username = State()
    amount = State()


class AdminSupportState(StatesGroup):
    url = State()


class AdminBroadcastState(StatesGroup):
    text = State()


class BetState(StatesGroup):
    amount = State()


@dp.message(CommandStart())
async def start(message: types.Message):
    get_user(message.from_user.id, message.from_user.username)

    args = message.text.split()
    payload = args[1] if len(args) > 1 else ""
    if payload.startswith("check_"):
        await activate_check(message, payload.replace("check_", ""))
        return
    if payload.startswith("ref_"):
        try:
            referrer_id = int(payload.replace("ref_", "", 1))
        except ValueError:
            referrer_id = 0
        if referrer_id and attach_referrer(message.from_user.id, referrer_id):
            await message.answer(
                f"{pe('referral', '👥')} Вы успешно пришли по реферальной ссылке.",
                parse_mode="HTML",
            )

    await message.answer(
        f"{pe('casino', '🎰')} <b>Добро пожаловать в X casino</b>\n\n"
        f"{pe('reserve', '🏦')} Резерв доступен по команде /reserv",
        parse_mode="HTML",
        reply_markup=bottom_menu(message.from_user.id),
    )


@dp.message(F.text.in_({"🎲 Играть", "Играть"}))
async def play_from_keyboard(message: types.Message):
    user = get_user(message.from_user.id, message.from_user.username)
    await message.answer(game_menu_text(user, 1), parse_mode="HTML", reply_markup=games_menu(1))


@dp.message(StateFilter(None), F.text.regexp(r"^\s*\d+(?:[.,]\d+)?\s*$"))
async def numeric_bet(message: types.Message):
    amount = parse_positive_float(message.text, MIN_BET)
    if amount is None:
        await message.answer(
            f"{pe('money', '💵')} Минимальная ставка: {money(MIN_BET)}.",
            parse_mode="HTML",
        )
        return

    set_current_bet(message.from_user.id, amount)
    user = get_user(message.from_user.id, message.from_user.username)
    await message.answer(
        f"{pe('money', '✅')} Ставка сохранена: <b>{money(amount)}</b>",
        parse_mode="HTML",
    )
    await message.answer(
        game_menu_text(user, 1),
        parse_mode="HTML",
        reply_markup=games_menu(1),
    )


@dp.message(F.text.in_({"💼 Баланс", "Баланс"}))
async def balance_from_keyboard(message: types.Message):
    user = get_user(message.from_user.id, message.from_user.username)
    await message.answer(profile_text(user), parse_mode="HTML", reply_markup=profile_menu())


@dp.message(F.text.in_({"☰ Меню", "🎰 Меню", "Меню"}))
async def menu_from_keyboard(message: types.Message):
    await message.answer(f"{pe('casino', '🎰')} <b>Меню казино</b>:", parse_mode="HTML", reply_markup=main_menu(message.from_user.id))


@dp.message(F.text.in_({"Админ-панель", "🛠 Админ-панель"}))
async def admin_from_keyboard(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer(admin_stats_text(), parse_mode="HTML", reply_markup=admin_menu())


@dp.callback_query(F.data == "back")
async def back(callback: types.CallbackQuery):
    await callback.message.edit_text(f"{pe('casino', '🎰')} <b>Меню казино</b>:", parse_mode="HTML", reply_markup=main_menu(callback.from_user.id))


@dp.callback_query(F.data == "profile")
async def profile(callback: types.CallbackQuery):
    user = get_user(callback.from_user.id, callback.from_user.username)
    await callback.message.edit_text(profile_text(user), parse_mode="HTML", reply_markup=profile_menu())


@dp.callback_query(F.data == "referral_info")
async def referral_info(callback: types.CallbackQuery):
    get_user(callback.from_user.id, callback.from_user.username)
    await callback.answer()
    await callback.message.edit_text(
        await referral_text(callback.from_user.id),
        parse_mode="HTML",
        reply_markup=referral_menu(),
    )


@dp.message(Command("reserv"))
async def reserve_command(message: types.Message):
    await message.answer(reserve_text(), parse_mode="HTML", reply_markup=main_menu(message.from_user.id))


@dp.callback_query(F.data == "reserve_view")
async def reserve_callback(callback: types.CallbackQuery):
    await callback.message.edit_text(reserve_text(), parse_mode="HTML", reply_markup=main_menu(callback.from_user.id))


pending_invoices = {}


@dp.callback_query(F.data == "deposit")
async def deposit(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer(
        f"{pe('deposit', '💳')} <b>Пополнение баланса</b>\n\nВведите сумму пополнения в USDT:",
        parse_mode="HTML",
    )
    await state.set_state(DepositState.amount)


@dp.message(DepositState.amount)
async def process_deposit(message: types.Message, state: FSMContext):
    amount = parse_positive_float(message.text, MIN_BET)
    if amount is None:
        await message.answer(f"{pe('money', '💵')} Введите сумму числом от {money(MIN_BET)}.", parse_mode="HTML")
        return

    invoice = await crypto.create_invoice(asset="USDT", amount=amount)
    pending_invoices[invoice.invoice_id] = {"user_id": message.from_user.id, "amount": amount}

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [ib("Оплатить счет", "deposit", url=invoice.bot_invoice_url)],
            [ib("Профиль", "profile", callback_data="profile")],
        ]
    )

    await message.answer(
        f"{pe('deposit', '💰')} <b>Счет создан</b>\n\n"
        f"Сумма: <b>{money(amount)}</b>\n"
        f"После оплаты баланс обновится автоматически.",
        parse_mode="HTML",
        reply_markup=kb,
    )
    await state.clear()


async def check_invoices():
    while True:
        try:
            invoices = await crypto.get_invoices(status="paid")
            for invoice in invoices.items:
                if invoice.invoice_id in pending_invoices:
                    data = pending_invoices.pop(invoice.invoice_id)
                    update_balance(data["user_id"], data["amount"])
                    record_transaction("deposit", data["amount"], data["user_id"], f"invoice:{invoice.invoice_id}")
                    await bot.send_message(
                        data["user_id"],
                        f"{pe('win', '✅')} Пополнение зачислено: <b>{money(data['amount'])}</b>",
                        parse_mode="HTML",
                        reply_markup=main_menu(data["user_id"]),
                    )
        except Exception as e:
            logging.error(f"Invoice check error: {e}")

        await asyncio.sleep(15)


@dp.callback_query(F.data == "withdraw")
async def withdraw(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer(f"{pe('withdraw', '💸')} Введите сумму вывода:", parse_mode="HTML")
    await state.set_state(WithdrawState.amount)


@dp.message(WithdrawState.amount)
async def withdraw_amount(message: types.Message, state: FSMContext):
    amount = parse_positive_float(message.text, MIN_BET)
    if amount is None:
        await message.answer(f"{pe('money', '💵')} Введите сумму числом от {money(MIN_BET)}.", parse_mode="HTML")
        return

    user = get_user(message.from_user.id, message.from_user.username)
    if user["balance"] < amount:
        await message.answer(f"{pe('wallet', '💼')} Недостаточно средств.", parse_mode="HTML")
        return
    if user["wager_progress"] < user["wager"]:
        left = user["wager"] - user["wager_progress"]
        await message.answer(f"{pe('chart', '📊')} Вагер не отыгран. Осталось: {money(left)}", parse_mode="HTML")
        return

    await state.update_data(amount=amount)
    await message.answer(f"{pe('wallet', '💼')} Введите ваш CryptoBot чек или кошелек:", parse_mode="HTML")
    await state.set_state(WithdrawState.wallet)


@dp.message(WithdrawState.wallet)
async def withdraw_wallet(message: types.Message, state: FSMContext):
    data = await state.get_data()
    amount = data["amount"]
    wallet = message.text.strip()
    if not wallet:
        await message.answer(f"{pe('wallet', '💼')} Кошелек не может быть пустым.", parse_mode="HTML")
        return

    update_balance(message.from_user.id, -amount)

    conn = db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO withdrawals (user_id, amount, wallet, created_at) VALUES (?, ?, ?, ?)",
        (message.from_user.id, amount, wallet, now_iso()),
    )
    withdrawal_id = cur.lastrowid
    conn.commit()
    conn.close()

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                ib("Подтвердить", "win", callback_data=f"approve_{withdrawal_id}"),
                ib("Отменить", "lose", callback_data=f"cancel_{withdrawal_id}"),
            ]
        ]
    )

    await bot.send_message(
        ADMIN_ID,
        f"{pe('withdraw', '💸')} <b>Новая заявка на вывод</b>\n\n"
        f"ID: <b>{withdrawal_id}</b>\n"
        f"Пользователь: <code>{message.from_user.id}</code>\n"
        f"Сумма: <b>{money(amount)}</b>\n"
        f"Кошелек: <code>{html.escape(wallet)}</code>",
        parse_mode="HTML",
        reply_markup=kb,
    )

    await message.answer(
        f"{pe('withdraw', '💸')} Заявка отправлена админу. Сумма временно заморожена.",
        parse_mode="HTML",
    )
    await state.clear()


@dp.callback_query(F.data.startswith("approve_"))
async def approve_withdraw(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        return

    withdrawal_id = int(callback.data.split("_")[1])
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT user_id, amount, status FROM withdrawals WHERE id=?", (withdrawal_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        await callback_notice(callback, "lose", "Заявка не найдена.")
        return
    if row["status"] != "pending":
        conn.close()
        await callback_notice(callback, "chart", "Заявка уже обработана.")
        return

    cur.execute("UPDATE withdrawals SET status='approved', processed_at=? WHERE id=?", (now_iso(), withdrawal_id))
    conn.commit()
    conn.close()

    record_transaction("withdraw_approved", row["amount"], row["user_id"], f"withdrawal:{withdrawal_id}")
    await bot.send_message(row["user_id"], f"{pe('win', '✅')} Ваш вывод подтвержден.", parse_mode="HTML")
    await callback.message.edit_text(f"{pe('win', '✅')} Вывод #{withdrawal_id} подтвержден.", parse_mode="HTML")


@dp.callback_query(F.data.startswith("cancel_"))
async def cancel_withdraw(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        return

    withdrawal_id = int(callback.data.split("_")[1])
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT user_id, amount, status FROM withdrawals WHERE id=?", (withdrawal_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        await callback_notice(callback, "lose", "Заявка не найдена.")
        return
    if row["status"] != "pending":
        conn.close()
        await callback_notice(callback, "chart", "Заявка уже обработана.")
        return

    cur.execute("UPDATE withdrawals SET status='cancelled', processed_at=? WHERE id=?", (now_iso(), withdrawal_id))
    conn.commit()
    conn.close()

    update_balance(row["user_id"], row["amount"])
    record_transaction("withdraw_cancelled", row["amount"], row["user_id"], f"withdrawal:{withdrawal_id}")
    await bot.send_message(row["user_id"], f"{pe('lose', '❌')} Вывод отменен, деньги возвращены.", parse_mode="HTML")
    await callback.message.edit_text(f"{pe('lose', '❌')} Вывод #{withdrawal_id} отменен.", parse_mode="HTML")


@dp.callback_query(F.data == "games")
async def games(callback: types.CallbackQuery):
    user = get_user(callback.from_user.id, callback.from_user.username)
    await callback.message.edit_text(game_menu_text(user, 1), parse_mode="HTML", reply_markup=games_menu(1))


@dp.callback_query(F.data.startswith("dice_"))
async def switch_dice_count(callback: types.CallbackQuery):
    dice_count = int(callback.data.split("_")[1])
    user = get_user(callback.from_user.id, callback.from_user.username)
    await callback.message.edit_text(game_menu_text(user, dice_count), parse_mode="HTML", reply_markup=games_menu(dice_count))


@dp.callback_query(F.data == "sum3_menu")
async def choose_sum3(callback: types.CallbackQuery):
    await callback.message.edit_text(
        f"{pe('dice', '🎲')} <b>Выберите сумму трех кубиков</b>",
        parse_mode="HTML",
        reply_markup=sum3_menu(),
    )


@dp.callback_query(F.data.startswith("set_bet_"))
async def choose_bet(callback: types.CallbackQuery, state: FSMContext):
    dice_count = int(callback.data.split("_")[2])
    await state.update_data(dice_count=dice_count)
    await callback.message.answer(
        f"{pe('money', '💵')} Введите новую ставку от <b>{money(MIN_BET)}</b>.\n"
        "Она сохранится и больше не будет запрашиваться перед каждой игрой.",
        parse_mode="HTML",
    )
    await state.set_state(BetState.amount)


@dp.message(BetState.amount)
async def save_bet(message: types.Message, state: FSMContext):
    amount = parse_positive_float(message.text, MIN_BET)
    if amount is None:
        await message.answer(
            f"{pe('money', '💵')} Введите ставку числом от {money(MIN_BET)}.",
            parse_mode="HTML",
        )
        return

    user_id = message.from_user.id
    set_current_bet(user_id, amount)
    data = await state.get_data()
    dice_count = data.get("dice_count", 1)
    await state.clear()

    user = get_user(user_id, message.from_user.username)
    await message.answer(
        f"{pe('money', '✅')} Ставка сохранена: <b>{money(amount)}</b>",
        parse_mode="HTML",
    )
    await message.answer(
        game_menu_text(user, dice_count),
        parse_mode="HTML",
        reply_markup=games_menu(dice_count),
    )


@dp.callback_query(F.data.startswith("game_"))
async def choose_game(callback: types.CallbackQuery):
    game_key = callback.data.replace("game_", "")
    game = GAMES.get(game_key)
    if not game:
        await callback_notice(callback, "dice", "Игра не найдена.")
        return

    user = get_user(callback.from_user.id, callback.from_user.username)
    amount = float(user["current_bet"] or MIN_BET)
    if amount < MIN_BET:
        amount = MIN_BET
    if user["balance"] < amount:
        await callback_notice(callback, "wallet", "Недостаточно средств на балансе.")
        return

    await callback.answer()
    await play_game_round(
        callback.message,
        callback.from_user,
        game_key,
        amount,
    )


@dp.callback_query(F.data.startswith("repeat_"))
async def repeat_game(callback: types.CallbackQuery):
    game_key = callback.data.replace("repeat_", "", 1)
    game = GAMES.get(game_key)
    if not game:
        await callback_notice(callback, "dice", "Игра не найдена.")
        return

    user = get_user(callback.from_user.id, callback.from_user.username)
    amount = max(MIN_BET, float(user["current_bet"] or MIN_BET))
    if user["balance"] < amount:
        await callback_notice(callback, "wallet", "Недостаточно средств на балансе.")
        return

    await callback.answer()
    await play_game_round(callback.message, callback.from_user, game_key, amount)


async def play_game_round(message: types.Message, telegram_user: types.User, game_key: str, amount: float):
    user_id = telegram_user.id
    game = GAMES[game_key]
    update_balance(user_id, -amount)
    await message.answer(
        f"{pe('dice', '🎲')} <b>Новая ставка принята</b>\n\n"
        f"{pe('profile', '👤')} Игрок: <b>{display_name(telegram_user)}</b>\n"
        f"{pe('dice', '🎯')} Игра: <b>{html.escape(game['title'])}</b>\n"
        f"{pe('money', '💵')} Ставка: <b>{money(amount)}</b>\n\n"
        f"<i>Удачи! Результат появится после броска.</i>",
        parse_mode="HTML",
    )

    dice_messages = []
    for _ in range(game["dice_count"]):
        dice_message = await message.answer_dice(emoji="🎲")
        dice_messages.append(dice_message)
        await asyncio.sleep(0.4)

    await asyncio.sleep(2.4)
    rolls = [dice_message.dice.value for dice_message in dice_messages]
    won = is_win(game_key, rolls)
    payout = amount * game["multiplier"] if won else 0.0

    update_wager_progress(user_id, amount)
    if won:
        update_balance(user_id, payout)

    record_game(user_id, game_key, amount, payout, rolls, won)
    if not won:
        await pay_referral_commission(telegram_user, game_key, amount)

    rolls_text = " + ".join(map(str, rolls))
    if len(rolls) == 1:
        product = rolls[0]
    elif len(rolls) == 2:
        product = rolls[0] * rolls[1]
    else:
        product = rolls[0] * rolls[1] * rolls[2]
    product_text = f"\nПроизведение: <b>{product}</b>"
    sum_text = f"\nСумма: <b>{sum(rolls)}</b>" if len(rolls) > 1 else ""

    if won:
        result = (
            f"{pe('win', '🎉')} <b>Победа!</b>\n\n"
            f"{pe('profile', '👤')} Игрок: <b>{display_name(telegram_user)}</b>\n"
            f"Игра: <b>{game['title']} x{game['multiplier']:g}</b>\n"
            f"Кубики: <b>{rolls_text}</b>{sum_text}{product_text}\n"
            f"{pe('money', '💵')} Ставка: <b>{money(amount)}</b>\n"
            f"Выигрыш: <b>{money(payout)}</b>"
        )
    else:
        result = (
            f"{pe('lose', '💀')} <b>Проигрыш</b>\n\n"
            f"{pe('profile', '👤')} Игрок: <b>{display_name(telegram_user)}</b>\n"
            f"Игра: <b>{game['title']} x{game['multiplier']:g}</b>\n"
            f"Кубики: <b>{rolls_text}</b>{sum_text}{product_text}\n"
            f"{pe('money', '💵')} Ставка: <b>{money(amount)}</b>"
        )

    await message.answer(result, parse_mode="HTML", reply_markup=result_menu(game_key))


@dp.message(Command("admin"))
async def admin_command(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer(admin_stats_text(), parse_mode="HTML", reply_markup=admin_menu())


def all_user_ids() -> list[int]:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users ORDER BY user_id")
    user_ids = [int(row["user_id"]) for row in cur.fetchall()]
    conn.close()
    return user_ids


async def start_broadcast(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        f"{pe('broadcast', '📣')} <b>Рассылка игрокам</b>\n\n"
        "Отправьте текст сообщения одним сообщением.\n"
        "Чтобы отменить, отправьте: <code>-</code>",
        parse_mode="HTML",
    )
    await state.set_state(AdminBroadcastState.text)


@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_callback(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await callback.answer()
    await start_broadcast(callback.message, state)


@dp.message(Command("broadcast"))
async def broadcast_command(message: types.Message, state: FSMContext):
    await start_broadcast(message, state)


@dp.message(AdminBroadcastState.text)
async def process_broadcast(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    text = (message.text or "").strip()
    if text in {"-", "/cancel", "отмена", "Отмена"}:
        await state.clear()
        await message.answer(
            f"{pe('broadcast', '📣')} Рассылка отменена.",
            parse_mode="HTML",
            reply_markup=admin_menu(),
        )
        return

    if not text:
        await message.answer(
            f"{pe('broadcast', '📣')} Отправьте непустой текст для рассылки.",
            parse_mode="HTML",
        )
        return

    user_ids = all_user_ids()
    if not user_ids:
        await state.clear()
        await message.answer(
            f"{pe('broadcast', '📣')} Зарегистрированных игроков пока нет.",
            parse_mode="HTML",
            reply_markup=admin_menu(),
        )
        return

    await message.answer(
        f"{pe('broadcast', '📣')} Рассылка запущена.\n"
        f"Получателей: <b>{len(user_ids)}</b>",
        parse_mode="HTML",
    )

    delivered = 0
    failed = 0
    broadcast_text = (
        f"{pe('broadcast', '📣')} <b>Сообщение от X casino</b>\n\n"
        f"{html.escape(text)}"
    )

    for user_id in user_ids:
        try:
            await bot.send_message(user_id, broadcast_text, parse_mode="HTML")
            delivered += 1
        except Exception as error:
            failed += 1
            logging.info("Broadcast failed for %s: %s", user_id, error)
        await asyncio.sleep(0.06)

    await state.clear()
    await message.answer(
        f"{pe('broadcast', '✅')} <b>Рассылка завершена</b>\n\n"
        f"Доставлено: <b>{delivered}</b>\n"
        f"Не доставлено: <b>{failed}</b>",
        parse_mode="HTML",
        reply_markup=admin_menu(),
    )


@dp.callback_query(F.data == "admin_open")
async def admin_open_callback(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback_notice(callback, "admin", "Нет доступа.")
        return
    await callback.answer()
    await callback.message.edit_text(admin_stats_text(), parse_mode="HTML", reply_markup=admin_menu())


@dp.callback_query(F.data == "admin_stats")
async def admin_stats_callback(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    await callback.answer()
    await callback.message.edit_text(admin_stats_text(), parse_mode="HTML", reply_markup=admin_menu())


@dp.callback_query(F.data == "admin_support")
async def admin_support_callback(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await callback.answer()
    current = get_setting("support_url").strip() or "не задана"
    await callback.message.answer(
        f"{pe('support', '🆘')} Текущая ссылка поддержки: <code>{html.escape(current)}</code>\n\n"
        "Отправьте новую ссылку (https://... или t.me/...).\n"
        "Чтобы удалить ссылку, отправьте: -",
        parse_mode="HTML",
    )
    await state.set_state(AdminSupportState.url)


@dp.message(AdminSupportState.url)
async def admin_support_url(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    value = message.text.strip()
    if value in {"-", "удалить", "удалить ссылку"}:
        set_setting("support_url", "")
        await message.answer(
            f"{pe('support', '🆘')} Ссылка поддержки удалена.",
            parse_mode="HTML",
            reply_markup=admin_menu(),
        )
        await state.clear()
        return

    if value.startswith("t.me/"):
        value = f"https://{value}"
    if not value.startswith(("https://", "http://")):
        await message.answer(
            f"{pe('support', '🆘')} Нужна ссылка формата <code>https://...</code> или <code>t.me/...</code>.",
            parse_mode="HTML",
        )
        return

    set_setting("support_url", value)
    await message.answer(
        f"{pe('support', '✅')} Ссылка поддержки сохранена.\n"
        f"{pe('casino', '🎰')} Она появится у игроков в кнопке «Меню».",
        parse_mode="HTML",
        reply_markup=admin_menu(),
    )
    await state.clear()


@dp.callback_query(F.data.in_({"admin_reserve_add", "admin_reserve_remove"}))
async def admin_reserve_action(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return

    await callback.answer()
    action = "add" if callback.data.endswith("add") else "remove"
    await state.update_data(action=action)
    await callback.message.answer(
        f"{pe('reserve', '🏦')} Введите сумму, на которую вы пополнили резерв:"
        if action == "add"
        else f"{pe('reserve', '🏦')} Введите сумму, которую вы сняли из резерва:",
        parse_mode="HTML",
    )
    await state.set_state(AdminReserveState.amount)


@dp.message(AdminReserveState.amount)
async def admin_reserve_amount(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    amount = parse_positive_float(message.text, 0.01)
    if amount is None:
        await message.answer(f"{pe('reserve', '🏦')} Введите сумму числом больше 0.", parse_mode="HTML")
        return

    data = await state.get_data()
    if data["action"] == "remove" and amount > get_setting_float("reserve"):
        await message.answer(
            f"{pe('reserve', '🏦')} В резерве сейчас только {money(get_setting_float('reserve'))}.",
            parse_mode="HTML",
        )
        return

    delta = amount if data["action"] == "add" else -amount
    reserve = change_reserve(delta)
    action_text = "пополнили" if delta > 0 else "сняли из"
    await message.answer(
        f"{pe('reserve', '🏦')} Вы {action_text} резерв: <b>{money(amount)}</b>\n"
        f"Текущий резерв: <b>{money(reserve)}</b>",
        parse_mode="HTML",
        reply_markup=admin_menu(),
    )
    await state.clear()


@dp.callback_query(F.data.in_({"admin_balance_add", "admin_balance_remove"}))
async def admin_balance_action(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return

    await callback.answer()
    operation = "add" if callback.data.endswith("add") else "remove"
    action_text = "выдать" if operation == "add" else "снять"
    await state.update_data(operation=operation)
    await callback.message.answer(
        f"{pe('admin', '🛠')} <b>Управление балансом игрока</b>\n\n"
        f"Введите username игрока, которому нужно {action_text} баланс.\n"
        "Можно отправить username с символом @.",
        parse_mode="HTML",
    )
    await state.set_state(AdminBalanceState.username)


@dp.message(AdminBalanceState.username)
async def admin_balance_username(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    username = normalize_username(message.text or "")
    if not username or " " in username:
        await message.answer(
            f"{pe('profile', '👤')} Введите корректный username, например "
            "<code>@player</code>.",
            parse_mode="HTML",
        )
        return

    target = get_user_by_username(username)
    if not target:
        await message.answer(
            f"{pe('profile', '👤')} Игрок <code>@{html.escape(username)}</code> не найден.\n"
            "Игрок должен хотя бы один раз открыть бота, чтобы его username сохранился.",
            parse_mode="HTML",
        )
        return

    await state.update_data(
        target_user_id=int(target["user_id"]),
        target_username=username,
    )
    data = await state.get_data()
    operation = data.get("operation")
    action_text = "выдать" if operation == "add" else "снять"
    await message.answer(
        f"{pe('profile', '👤')} Игрок: <b>@{html.escape(username)}</b>\n"
        f"{pe('wallet', '💼')} Текущий баланс: "
        f"<b>{money(float(target['balance'] or 0))}</b>\n\n"
        f"{pe('money', '💵')} Введите сумму, которую нужно {action_text}:",
        parse_mode="HTML",
    )
    await state.set_state(AdminBalanceState.amount)


@dp.message(AdminBalanceState.amount)
async def admin_balance_amount(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    amount = parse_positive_float(message.text or "", 0.01)
    if amount is None:
        await message.answer(
            f"{pe('money', '💵')} Введите сумму числом больше 0.",
            parse_mode="HTML",
        )
        return

    data = await state.get_data()
    target_user_id = data.get("target_user_id")
    target_username = normalize_username(data.get("target_username", ""))
    operation = data.get("operation")
    if not target_user_id or operation not in {"add", "remove"}:
        await state.clear()
        await message.answer(
            f"{pe('admin', '🛠')} Сессия управления балансом устарела. "
            "Откройте действие заново.",
            parse_mode="HTML",
            reply_markup=admin_menu(),
        )
        return

    target_user_id = int(target_user_id)
    if operation == "remove":
        if not debit_balance(target_user_id, amount):
            current = float(get_user(target_user_id)["balance"] or 0)
            await message.answer(
                f"{pe('money', '💵')} Нельзя снять <b>{money(amount)}</b>.\n"
                f"{pe('wallet', '💼')} Доступно только: <b>{money(current)}</b>",
                parse_mode="HTML",
            )
            return
        kind = "admin_balance_remove"
        delta_text = f"-{money(amount)}"
    else:
        update_balance(target_user_id, amount)
        kind = "admin_balance_grant"
        delta_text = f"+{money(amount)}"

    updated = get_user(target_user_id)
    new_balance = float(updated["balance"] or 0)
    record_transaction(
        kind,
        amount,
        target_user_id,
        f"admin:{message.from_user.id};username:{target_username}",
    )

    await message.answer(
        f"{pe('admin', '🛠')} <b>Баланс изменён</b>\n\n"
        f"{pe('profile', '👤')} Игрок: <b>@{html.escape(target_username)}</b>\n"
        f"{pe('money', '💵')} Изменение: <b>{delta_text}</b>\n"
        f"{pe('wallet', '💼')} Новый баланс: <b>{money(new_balance)}</b>",
        parse_mode="HTML",
        reply_markup=admin_menu(),
    )
    try:
        if operation == "add":
            target_text = (
                f"{pe('money', '💵')} Администратор начислил вам "
                f"<b>{money(amount)}</b>.\n"
                f"{pe('wallet', '💼')} Новый баланс: <b>{money(new_balance)}</b>"
            )
        else:
            target_text = (
                f"{pe('withdraw', '💸')} Администратор снял с вашего баланса "
                f"<b>{money(amount)}</b>.\n"
                f"{pe('wallet', '💼')} Новый баланс: <b>{money(new_balance)}</b>"
            )
        await bot.send_message(target_user_id, target_text, parse_mode="HTML")
    except Exception as error:
        logging.info("Admin balance notification failed for %s: %s", target_user_id, error)
    await state.clear()


async def start_check_creation(target_message: types.Message, state: FSMContext, creator_id: int):
    await state.update_data(creator_id=creator_id)
    await target_message.answer(
        f"{pe('gift', '🎁')} <b>Создание подарочного чека</b>\n\n"
        "Введите сумму, которую получит один активировавший игрок:",
        parse_mode="HTML",
    )
    await state.set_state(AdminCheckState.amount)


@dp.callback_query(F.data == "referral_createcheck")
async def referral_createcheck_callback(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await start_check_creation(callback.message, state, callback.from_user.id)


@dp.callback_query(F.data == "admin_createcheck")
async def admin_createcheck_callback(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await callback.answer()
    await start_check_creation(callback.message, state, callback.from_user.id)


@dp.message(Command("createcheck"))
async def create_check(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await start_check_creation(message, state, message.from_user.id)


@dp.message(AdminCheckState.amount)
async def check_amount(message: types.Message, state: FSMContext):
    amount = parse_positive_float(message.text, 0.01)
    if amount is None:
        await message.answer(f"{pe('money', '💵')} Введите сумму числом больше 0.", parse_mode="HTML")
        return
    await state.update_data(amount=amount)
    await message.answer(f"{pe('gift', '🎁')} Введите количество активаций:", parse_mode="HTML")
    await state.set_state(AdminCheckState.activations)


@dp.message(AdminCheckState.activations)
async def check_activations(message: types.Message, state: FSMContext):
    try:
        activations = int(message.text.strip())
    except ValueError:
        await message.answer(f"{pe('gift', '🎁')} Введите целое число активаций.", parse_mode="HTML")
        return
    if activations < 1:
        await message.answer(f"{pe('gift', '🎁')} Активаций должно быть минимум 1.", parse_mode="HTML")
        return
    await state.update_data(activations=activations)
    await message.answer(f"{pe('chart', '📊')} Введите вагер:", parse_mode="HTML")
    await state.set_state(AdminCheckState.wager)


@dp.message(AdminCheckState.wager)
async def check_wager(message: types.Message, state: FSMContext):
    wager = parse_positive_float(message.text, 0.0)
    if wager is None:
        await message.answer(f"{pe('chart', '📊')} Введите вагер числом от 0.", parse_mode="HTML")
        return

    await state.update_data(wager=wager)
    await message.answer(
        f"{pe('gift', '🎁')} Для кого создать этот чек?\n\n"
        "Стоимость чека списывается с баланса создателя:\n"
        "<b>сумма одного приза × количество активаций</b>.",
        parse_mode="HTML",
    )
    await message.answer(
        f"{pe('gift', '🎁')} Выберите доступность чека:",
        parse_mode="HTML",
        reply_markup=check_scope_menu(),
    )
    await state.set_state(AdminCheckState.scope)


async def finish_check_creation(callback: types.CallbackQuery, state: FSMContext, referral_only: bool):
    data = await state.get_data()
    creator_id = data.get("creator_id")
    if creator_id != callback.from_user.id:
        await callback_notice(callback, "gift", "Сессия создания чека устарела.")
        await state.clear()
        return

    amount = float(data["amount"])
    activations = int(data["activations"])
    wager = float(data["wager"])
    total_cost = round(amount * activations, 2)
    creator = get_user(creator_id, callback.from_user.username)

    if creator["balance"] < total_cost:
        await callback.answer()
        await callback.message.answer(
            f"{pe('money', '💵')} Недостаточно средств.\n"
            f"Нужно: <b>{money(total_cost)}</b>\n"
            f"Доступно: <b>{money(creator['balance'])}</b>",
            parse_mode="HTML",
        )
        await state.clear()
        return

    code = f"CHK{secrets.randbelow(900000) + 100000}"
    if not debit_balance(creator_id, total_cost):
        await callback.answer()
        await callback.message.answer(
            f"{pe('money', '💵')} Не удалось списать стоимость чека. Повторите попытку.",
            parse_mode="HTML",
        )
        await state.clear()
        return
    record_transaction(
        "check_create",
        total_cost,
        creator_id,
        f"check:{code};referral_only:{int(referral_only)}",
    )

    conn = db()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO checks
           (code, amount, activations, wager, creator_id, referral_only)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (code, amount, activations, wager, creator_id, int(referral_only)),
    )
    conn.commit()
    conn.close()

    bot_info = await bot.get_me()
    link = f"https://t.me/{bot_info.username}?start=check_{code}"
    audience = "только ваши рефералы" if referral_only else "все игроки"
    await callback.answer()
    await callback.message.answer(
        f"{pe('gift', '🎁')} <b>Чек создан</b>\n\n"
        f"{pe('money', '💵')} Приз одному игроку: <b>{money(amount)}</b>\n"
        f"{pe('gift', '🎁')} Активаций: <b>{activations}</b>\n"
        f"{pe('chart', '📊')} Вагер: <b>{money(wager)}</b>\n"
        f"{pe('referral', '👥')} Доступ: <b>{audience}</b>\n"
        f"{pe('money', '💵')} Списано с баланса: <b>{money(total_cost)}</b>\n\n"
        f"Ссылка:\n<code>{link}</code>",
        parse_mode="HTML",
        reply_markup=admin_menu() if is_admin(creator_id) else referral_menu(),
    )
    await state.clear()


@dp.callback_query(F.data == "check_scope_referrals")
async def check_scope_referrals(callback: types.CallbackQuery, state: FSMContext):
    await finish_check_creation(callback, state, referral_only=True)


@dp.callback_query(F.data == "check_scope_all")
async def check_scope_all(callback: types.CallbackQuery, state: FSMContext):
    await finish_check_creation(callback, state, referral_only=False)


async def activate_check(message: types.Message, code: str):
    conn = db()
    cur = conn.cursor()

    cur.execute(
        "SELECT id, amount, activations, used, wager, creator_id, referral_only FROM checks WHERE code=?",
        (code,),
    )
    check = cur.fetchone()
    if not check:
        conn.close()
        await message.answer(f"{pe('gift', '🎁')} Чек не найден.", parse_mode="HTML")
        return

    check_id = check["id"]
    amount = check["amount"]
    activations = check["activations"]
    used = check["used"]
    wager = check["wager"]
    creator_id = check["creator_id"]
    referral_only = bool(check["referral_only"])

    if used >= activations:
        conn.close()
        await message.answer(f"{pe('gift', '🎁')} Чек закончился.", parse_mode="HTML")
        return

    if referral_only and (not creator_id or get_referrer_id(message.from_user.id) != creator_id):
        conn.close()
        await message.answer(
            f"{pe('referral', '👥')} Этот чек доступен только рефералам его создателя.",
            parse_mode="HTML",
        )
        return

    cur.execute(
        "SELECT 1 FROM check_activations WHERE user_id=? AND check_id=?",
        (message.from_user.id, check_id),
    )
    if cur.fetchone():
        conn.close()
        await message.answer(f"{pe('gift', '🎁')} Вы уже активировали этот чек.", parse_mode="HTML")
        return

    cur.execute(
        "UPDATE users SET balance = balance + ?, wager = wager + ? WHERE user_id=?",
        (amount, wager, message.from_user.id),
    )
    cur.execute("UPDATE checks SET used = used + 1 WHERE id=?", (check_id,))
    cur.execute(
        "INSERT INTO check_activations (user_id, check_id) VALUES (?, ?)",
        (message.from_user.id, check_id),
    )
    conn.commit()
    conn.close()

    record_transaction("check_activation", amount, message.from_user.id, f"check:{code}")
    await message.answer(
        f"{pe('gift', '🎁')} Вы получили <b>{money(amount)}</b>\n"
        f"{pe('dice', '🎯')} Вагер: <b>{money(wager)}</b>",
        parse_mode="HTML",
        reply_markup=main_menu(message.from_user.id),
    )


async def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set")
    if not CRYPTOBOT_TOKEN:
        raise RuntimeError("CRYPTOBOT_TOKEN is not set")
    if not ADMIN_ID:
        raise RuntimeError("ADMIN_ID is not set")

    asyncio.create_task(check_invoices())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
