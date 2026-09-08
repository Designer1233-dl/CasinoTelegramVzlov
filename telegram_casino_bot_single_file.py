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
from aiogram.filters import Command, CommandStart
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
}


def pe(key: str, fallback: str) -> str:
    emoji_id = PREMIUM_EMOJI_IDS.get(key)
    if emoji_id:
        return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'
    return fallback


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
            username TEXT
        )"""
    )

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
            wager REAL DEFAULT 0
        )"""
    )

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

    conn.commit()
    conn.close()


init_db()


def now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def money(value: float) -> str:
    return f"${value:.2f}"


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


def update_balance(user_id: int, amount: float):
    get_user(user_id)
    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, user_id))
    conn.commit()
    conn.close()


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


def get_setting_float(key: str, default: float = 0.0) -> float:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return default
    try:
        return float(row["value"])
    except ValueError:
        return default


def set_setting_float(key: str, value: float):
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )
    conn.commit()
    conn.close()


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


def bottom_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="💼 Баланс"),
                KeyboardButton(text="🎲 Играть"),
                KeyboardButton(text="☰ Меню"),
            ]
        ],
        resize_keyboard=True,
    )


def main_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎲 Играть", callback_data="games")],
            [InlineKeyboardButton(text="👤 Профиль", callback_data="profile")],
            [InlineKeyboardButton(text="🏦 Резерв", callback_data="reserve_view")],
        ]
    )


def profile_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Пополнить", callback_data="deposit")],
            [InlineKeyboardButton(text="💸 Вывести", callback_data="withdraw")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back")],
        ]
    )


def games_menu(dice_count: int):
    one_active = "🔵 1 Бросок" if dice_count == 1 else "1 Бросок"
    two_active = "🔵 2 Броска" if dice_count == 2 else "2 Броска"
    three_active = "🔵 3 Броска" if dice_count == 3 else "3 Броска"

    rows = [
        [
            InlineKeyboardButton(text=one_active, callback_data="dice_1"),
            InlineKeyboardButton(text=two_active, callback_data="dice_2"),
            InlineKeyboardButton(text=three_active, callback_data="dice_3"),
        ]
    ]

    if dice_count == 1:
        rows += [
            [
                InlineKeyboardButton(text="Чет (x2)", callback_data="game_d1_even"),
                InlineKeyboardButton(text="Нечет (x2)", callback_data="game_d1_odd"),
            ],
            [
                InlineKeyboardButton(text="Меньше (x2)", callback_data="game_d1_less"),
                InlineKeyboardButton(text="Больше (x2)", callback_data="game_d1_more"),
            ],
            [
                InlineKeyboardButton(text="1 (x6)", callback_data="game_d1_exact_1"),
                InlineKeyboardButton(text="2 (x6)", callback_data="game_d1_exact_2"),
                InlineKeyboardButton(text="3 (x6)", callback_data="game_d1_exact_3"),
            ],
            [
                InlineKeyboardButton(text="4 (x6)", callback_data="game_d1_exact_4"),
                InlineKeyboardButton(text="5 (x6)", callback_data="game_d1_exact_5"),
                InlineKeyboardButton(text="6 (x6)", callback_data="game_d1_exact_6"),
            ],
        ]
    elif dice_count == 2:
        rows += [
            [
                InlineKeyboardButton(text="Оба четных (x4)", callback_data="game_d2_even"),
                InlineKeyboardButton(text="Оба нечет (x4)", callback_data="game_d2_odd"),
            ],
            [
                InlineKeyboardButton(text="Оба меньше 4 (x3.77)", callback_data="game_d2_less"),
                InlineKeyboardButton(text="Оба больше 3 (x3.77)", callback_data="game_d2_more"),
            ],
            [
                InlineKeyboardButton(text="Куб 1 > 2 (x2.4)", callback_data="game_d2_first_gt"),
                InlineKeyboardButton(text="Куб 1 < 2 (x2.4)", callback_data="game_d2_first_lt"),
            ],
            [
                InlineKeyboardButton(text="1 (x36)", callback_data="game_d2_exact_1"),
                InlineKeyboardButton(text="2 (x36)", callback_data="game_d2_exact_2"),
                InlineKeyboardButton(text="3 (x36)", callback_data="game_d2_exact_3"),
            ],
            [
                InlineKeyboardButton(text="4 (x36)", callback_data="game_d2_exact_4"),
                InlineKeyboardButton(text="5 (x36)", callback_data="game_d2_exact_5"),
                InlineKeyboardButton(text="6 (x36)", callback_data="game_d2_exact_6"),
            ],
            [
                InlineKeyboardButton(text="Любой дубль (x6)", callback_data="game_d2_double"),
                InlineKeyboardButton(text="Произвед > 18 (x3.9)", callback_data="game_d2_product_18"),
            ],
        ]
    else:
        rows += [
            [
                InlineKeyboardButton(text="Три четных (x8)", callback_data="game_d3_even"),
                InlineKeyboardButton(text="Три нечет (x8)", callback_data="game_d3_odd"),
            ],
            [
                InlineKeyboardButton(text="Три меньше (x8)", callback_data="game_d3_less"),
                InlineKeyboardButton(text="Три больше (x8)", callback_data="game_d3_more"),
            ],
            [
                InlineKeyboardButton(text="Убывание (x10.8)", callback_data="game_d3_down"),
                InlineKeyboardButton(text="Возрастание (x10.8)", callback_data="game_d3_up"),
            ],
            [
                InlineKeyboardButton(text="1 (x216)", callback_data="game_d3_exact_1"),
                InlineKeyboardButton(text="2 (x216)", callback_data="game_d3_exact_2"),
                InlineKeyboardButton(text="3 (x216)", callback_data="game_d3_exact_3"),
            ],
            [
                InlineKeyboardButton(text="4 (x216)", callback_data="game_d3_exact_4"),
                InlineKeyboardButton(text="5 (x216)", callback_data="game_d3_exact_5"),
                InlineKeyboardButton(text="6 (x216)", callback_data="game_d3_exact_6"),
            ],
            [
                InlineKeyboardButton(text="Сумма (до x54)", callback_data="sum3_menu"),
                InlineKeyboardButton(text="Любой трипл (x36)", callback_data="game_d3_any_triple"),
            ],
            [
                InlineKeyboardButton(text="Дуэль (x3.92)", callback_data="game_d3_duel"),
                InlineKeyboardButton(text="Стрит (x9)", callback_data="game_d3_straight"),
            ],
            [InlineKeyboardButton(text="Произвед > 108 (x12.7)", callback_data="game_d3_product_108")],
        ]

    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def sum3_menu():
    rows = []
    for left, right in [(3, 18), (4, 17), (5, 16), (6, 15), (7, 14), (8, 13), (9, 12), (10, 11)]:
        rows.append(
            [
                InlineKeyboardButton(text=f"{left} (x{SUM_MULTIPLIERS_3D[left]:g})", callback_data=f"game_d3_sum_{left}"),
                InlineKeyboardButton(text=f"{right} (x{SUM_MULTIPLIERS_3D[right]:g})", callback_data=f"game_d3_sum_{right}"),
            ]
        )
    rows.append([InlineKeyboardButton(text="⬅️ К 3 броскам", callback_data="dice_3")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
            [
                InlineKeyboardButton(text="🏦 Я пополнил резерв", callback_data="admin_reserve_add"),
                InlineKeyboardButton(text="💸 Я снял резерв", callback_data="admin_reserve_remove"),
            ],
            [InlineKeyboardButton(text="🎁 Создать чек", callback_data="admin_createcheck")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back")],
        ]
    )


def game_menu_text(user, dice_count: int) -> str:
    return (
        f"{pe('dice', '🎲')} <b>Выберите исход игры</b>\n\n"
        f"{pe('money', '💵')} Ставка: <b>после выбора</b>\n"
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
        f"До вывода осталось: <b>{money(left)}</b>"
    )


def reserve_text() -> str:
    reserve = get_setting_float("reserve")
    return f"{pe('reserve', '🏦')} <b>Резерв казино</b>\n\nТекущий резерв: <b>{money(reserve)}</b>"


def admin_stats_text() -> str:
    stats = casino_stats()
    sign = "+" if stats["game_profit"] >= 0 else ""
    return (
        f"{pe('admin', '🛠')} <b>Админ-панель</b>\n\n"
        f"{pe('chart', '📊')} Игры: <b>{stats['games']}</b>\n"
        f"Ставки игроков: <b>{money(stats['bets'])}</b>\n"
        f"Выплаты по играм: <b>{money(stats['payouts'])}</b>\n"
        f"Плюс/минус казино по играм: <b>{sign}{money(stats['game_profit'])}</b>\n\n"
        f"{pe('money', '💳')} Пополнения игроков: <b>{money(stats['deposits'])}</b>\n"
        f"{pe('wallet', '💸')} Подтвержденные выводы: <b>{money(stats['approved_withdraws'])}</b>\n"
        f"Заявки на вывод в ожидании: <b>{money(stats['pending_withdraws'])}</b>\n\n"
        f"{pe('profile', '👥')} Игроков: <b>{stats['users']}</b>\n"
        f"Баланс у игроков всего: <b>{money(stats['balances'])}</b>\n"
        f"{pe('reserve', '🏦')} Резерв: <b>{money(stats['reserve'])}</b>"
    )


class DepositState(StatesGroup):
    amount = State()


class WithdrawState(StatesGroup):
    amount = State()
    wallet = State()


class GameState(StatesGroup):
    amount = State()
    game_key = State()


class AdminCheckState(StatesGroup):
    amount = State()
    activations = State()
    wager = State()


class AdminReserveState(StatesGroup):
    amount = State()
    action = State()


@dp.message(CommandStart())
async def start(message: types.Message):
    get_user(message.from_user.id, message.from_user.username)

    args = message.text.split()
    if len(args) > 1 and args[1].startswith("check_"):
        code = args[1].replace("check_", "")
        await activate_check(message, code)
        return

    await message.answer(
        f"{pe('casino', '🎰')} <b>Добро пожаловать в Casino Bot</b>\n\n"
        f"{pe('dice', '🎲')} Реальные Telegram-кубы: 1, 2 или 3 броска\n"
        f"{pe('money', '💵')} Минимальная ставка: <b>{money(MIN_BET)}</b>\n"
        f"{pe('reserve', '🏦')} Резерв доступен по команде /reserv",
        parse_mode="HTML",
        reply_markup=bottom_menu(),
    )
    await message.answer("Главное меню:", reply_markup=main_menu())


@dp.message(F.text.in_({"🎲 Играть", "Играть"}))
async def play_from_keyboard(message: types.Message):
    user = get_user(message.from_user.id, message.from_user.username)
    await message.answer(game_menu_text(user, 1), parse_mode="HTML", reply_markup=games_menu(1))


@dp.message(F.text.in_({"💼 Баланс", "Баланс"}))
async def balance_from_keyboard(message: types.Message):
    user = get_user(message.from_user.id, message.from_user.username)
    await message.answer(profile_text(user), parse_mode="HTML", reply_markup=profile_menu())


@dp.message(F.text.in_({"☰ Меню", "Меню"}))
async def menu_from_keyboard(message: types.Message):
    await message.answer("Главное меню:", reply_markup=main_menu())


@dp.callback_query(F.data == "back")
async def back(callback: types.CallbackQuery):
    await callback.message.edit_text("Главное меню:", reply_markup=main_menu())


@dp.callback_query(F.data == "profile")
async def profile(callback: types.CallbackQuery):
    user = get_user(callback.from_user.id, callback.from_user.username)
    await callback.message.edit_text(profile_text(user), parse_mode="HTML", reply_markup=profile_menu())


@dp.message(Command("reserv"))
async def reserve_command(message: types.Message):
    await message.answer(reserve_text(), parse_mode="HTML", reply_markup=main_menu())


@dp.callback_query(F.data == "reserve_view")
async def reserve_callback(callback: types.CallbackQuery):
    await callback.message.edit_text(reserve_text(), parse_mode="HTML", reply_markup=main_menu())


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
        await message.answer(f"Введите сумму числом от {money(MIN_BET)}.")
        return

    invoice = await crypto.create_invoice(asset="USDT", amount=amount)
    pending_invoices[invoice.invoice_id] = {"user_id": message.from_user.id, "amount": amount}

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оплатить счет", url=invoice.bot_invoice_url)],
            [InlineKeyboardButton(text="👤 Профиль", callback_data="profile")],
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
                        reply_markup=main_menu(),
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
        await message.answer(f"Введите сумму числом от {money(MIN_BET)}.")
        return

    user = get_user(message.from_user.id, message.from_user.username)
    if user["balance"] < amount:
        await message.answer("Недостаточно средств.")
        return
    if user["wager_progress"] < user["wager"]:
        left = user["wager"] - user["wager_progress"]
        await message.answer(f"Вагер не отыгран. Осталось: {money(left)}")
        return

    await state.update_data(amount=amount)
    await message.answer("Введите ваш CryptoBot чек или кошелек:")
    await state.set_state(WithdrawState.wallet)


@dp.message(WithdrawState.wallet)
async def withdraw_wallet(message: types.Message, state: FSMContext):
    data = await state.get_data()
    amount = data["amount"]
    wallet = message.text.strip()
    if not wallet:
        await message.answer("Кошелек не может быть пустым.")
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
                InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"approve_{withdrawal_id}"),
                InlineKeyboardButton(text="❌ Отменить", callback_data=f"cancel_{withdrawal_id}"),
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

    await message.answer("Заявка отправлена админу. Сумма временно заморожена.")
    await state.clear()


@dp.callback_query(F.data.startswith("approve_"))
async def approve_withdraw(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return

    withdrawal_id = int(callback.data.split("_")[1])
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT user_id, amount, status FROM withdrawals WHERE id=?", (withdrawal_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    if row["status"] != "pending":
        conn.close()
        await callback.answer("Заявка уже обработана", show_alert=True)
        return

    cur.execute("UPDATE withdrawals SET status='approved', processed_at=? WHERE id=?", (now_iso(), withdrawal_id))
    conn.commit()
    conn.close()

    record_transaction("withdraw_approved", row["amount"], row["user_id"], f"withdrawal:{withdrawal_id}")
    await bot.send_message(row["user_id"], f"{pe('win', '✅')} Ваш вывод подтвержден.", parse_mode="HTML")
    await callback.message.edit_text(f"Вывод #{withdrawal_id} подтвержден.")


@dp.callback_query(F.data.startswith("cancel_"))
async def cancel_withdraw(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return

    withdrawal_id = int(callback.data.split("_")[1])
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT user_id, amount, status FROM withdrawals WHERE id=?", (withdrawal_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    if row["status"] != "pending":
        conn.close()
        await callback.answer("Заявка уже обработана", show_alert=True)
        return

    cur.execute("UPDATE withdrawals SET status='cancelled', processed_at=? WHERE id=?", (now_iso(), withdrawal_id))
    conn.commit()
    conn.close()

    update_balance(row["user_id"], row["amount"])
    record_transaction("withdraw_cancelled", row["amount"], row["user_id"], f"withdrawal:{withdrawal_id}")
    await bot.send_message(row["user_id"], f"{pe('lose', '❌')} Вывод отменен, деньги возвращены.", parse_mode="HTML")
    await callback.message.edit_text(f"Вывод #{withdrawal_id} отменен.")


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


@dp.callback_query(F.data.startswith("game_"))
async def choose_game(callback: types.CallbackQuery, state: FSMContext):
    game_key = callback.data.replace("game_", "")
    game = GAMES.get(game_key)
    if not game:
        await callback.answer("Игра не найдена", show_alert=True)
        return

    await state.update_data(game_key=game_key)
    await callback.message.answer(
        f"{pe('dice', '🎲')} <b>{game['title']} x{game['multiplier']:g}</b>\n"
        f"Условие: {game['description']}.\n\n"
        f"Введите ставку от <b>{money(MIN_BET)}</b>:",
        parse_mode="HTML",
    )
    await state.set_state(GameState.amount)


@dp.message(GameState.amount)
async def play_game(message: types.Message, state: FSMContext):
    amount = parse_positive_float(message.text, MIN_BET)
    if amount is None:
        await message.answer(f"Введите ставку числом от {money(MIN_BET)}.")
        return

    user = get_user(message.from_user.id, message.from_user.username)
    if user["balance"] < amount:
        await message.answer("Недостаточно средств.")
        return

    data = await state.get_data()
    game_key = data["game_key"]
    game = GAMES[game_key]

    update_balance(message.from_user.id, -amount)
    await message.answer(f"{pe('dice', '🎲')} Бросаю реальные кубики Telegram...")

    dice_messages = []
    for _ in range(game["dice_count"]):
        dice_message = await message.answer_dice(emoji="🎲")
        dice_messages.append(dice_message)
        await asyncio.sleep(0.4)

    await asyncio.sleep(2.4)
    rolls = [dice_message.dice.value for dice_message in dice_messages]
    won = is_win(game_key, rolls)
    payout = amount * game["multiplier"] if won else 0.0

    update_wager_progress(message.from_user.id, amount)
    if won:
        update_balance(message.from_user.id, payout)

    record_game(message.from_user.id, game_key, amount, payout, rolls, won)

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
            f"Игра: <b>{game['title']} x{game['multiplier']:g}</b>\n"
            f"Кубики: <b>{rolls_text}</b>{sum_text}{product_text}\n"
            f"Выигрыш: <b>{money(payout)}</b>"
        )
    else:
        result = (
            f"{pe('lose', '💀')} <b>Проигрыш</b>\n\n"
            f"Игра: <b>{game['title']} x{game['multiplier']:g}</b>\n"
            f"Кубики: <b>{rolls_text}</b>{sum_text}{product_text}\n"
            f"Ставка: <b>{money(amount)}</b>"
        )

    await message.answer(result, parse_mode="HTML", reply_markup=main_menu())
    await state.clear()


@dp.message(Command("admin"))
async def admin_command(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer(admin_stats_text(), parse_mode="HTML", reply_markup=admin_menu())


@dp.callback_query(F.data == "admin_stats")
async def admin_stats_callback(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    await callback.message.edit_text(admin_stats_text(), parse_mode="HTML", reply_markup=admin_menu())


@dp.callback_query(F.data.in_({"admin_reserve_add", "admin_reserve_remove"}))
async def admin_reserve_action(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return

    action = "add" if callback.data.endswith("add") else "remove"
    await state.update_data(action=action)
    await callback.message.answer(
        "Введите сумму, на которую вы пополнили резерв:"
        if action == "add"
        else "Введите сумму, которую вы сняли из резерва:"
    )
    await state.set_state(AdminReserveState.amount)


@dp.message(AdminReserveState.amount)
async def admin_reserve_amount(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await state.clear()
        return

    amount = parse_positive_float(message.text, 0.01)
    if amount is None:
        await message.answer("Введите сумму числом больше 0.")
        return

    data = await state.get_data()
    if data["action"] == "remove" and amount > get_setting_float("reserve"):
        await message.answer(f"В резерве сейчас только {money(get_setting_float('reserve'))}.")
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


@dp.callback_query(F.data == "admin_createcheck")
async def admin_createcheck_callback(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return
    await callback.message.answer("Введите сумму чека:")
    await state.set_state(AdminCheckState.amount)


@dp.message(Command("createcheck"))
async def create_check(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("Введите сумму чека:")
    await state.set_state(AdminCheckState.amount)


@dp.message(AdminCheckState.amount)
async def check_amount(message: types.Message, state: FSMContext):
    amount = parse_positive_float(message.text, 0.01)
    if amount is None:
        await message.answer("Введите сумму числом больше 0.")
        return
    await state.update_data(amount=amount)
    await message.answer("Введите количество активаций:")
    await state.set_state(AdminCheckState.activations)


@dp.message(AdminCheckState.activations)
async def check_activations(message: types.Message, state: FSMContext):
    try:
        activations = int(message.text.strip())
    except ValueError:
        await message.answer("Введите целое число активаций.")
        return
    if activations < 1:
        await message.answer("Активаций должно быть минимум 1.")
        return
    await state.update_data(activations=activations)
    await message.answer("Введите вагер:")
    await state.set_state(AdminCheckState.wager)


@dp.message(AdminCheckState.wager)
async def check_wager(message: types.Message, state: FSMContext):
    wager = parse_positive_float(message.text, 0.0)
    if wager is None:
        await message.answer("Введите вагер числом от 0.")
        return

    data = await state.get_data()
    amount = data["amount"]
    activations = data["activations"]
    code = f"CHK{secrets.randbelow(900000) + 100000}"

    conn = db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO checks (code, amount, activations, wager) VALUES (?, ?, ?, ?)",
        (code, amount, activations, wager),
    )
    conn.commit()
    conn.close()

    bot_info = await bot.get_me()
    link = f"https://t.me/{bot_info.username}?start=check_{code}"

    await message.answer(
        f"{pe('gift', '🎁')} <b>Чек создан</b>\n\n"
        f"Сумма: <b>{money(amount)}</b>\n"
        f"Активаций: <b>{activations}</b>\n"
        f"Вагер: <b>{money(wager)}</b>\n\n"
        f"{link}",
        parse_mode="HTML",
        reply_markup=admin_menu(),
    )
    await state.clear()


async def activate_check(message: types.Message, code: str):
    conn = db()
    cur = conn.cursor()

    cur.execute("SELECT id, amount, activations, used, wager FROM checks WHERE code=?", (code,))
    check = cur.fetchone()
    if not check:
        conn.close()
        await message.answer("Чек не найден.")
        return

    check_id = check["id"]
    amount = check["amount"]
    activations = check["activations"]
    used = check["used"]
    wager = check["wager"]

    if used >= activations:
        conn.close()
        await message.answer("Чек закончился.")
        return

    cur.execute(
        "SELECT 1 FROM check_activations WHERE user_id=? AND check_id=?",
        (message.from_user.id, check_id),
    )
    if cur.fetchone():
        conn.close()
        await message.answer("Вы уже активировали этот чек.")
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
        reply_markup=main_menu(),
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
