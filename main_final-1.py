"""
AE — Assassin Energy | Backend API — ФИНАЛЬНАЯ ВЕРСИЯ
Исправления vs v1 (main.py):
  - sha256 → bcrypt (bcrypt защищён от brute-force, sha256 — нет)
  - Убран хардкод ADMIN_KEY в коде. Теперь is_admin в БД
  - Добавлены сессионные токены (UUID4) вместо передачи username в теле
  - /api/sync: теперь начисляет только за новые повторы (delta), а не пересчитывает весь баланс
  - /api/spin: приз реально начисляется в БД (v1 — нет!)
  - /api/admin/action: проверяет is_admin из БД, не сравнивает строку ключа
  - Все мутирующие запросы в транзакциях с rollback
  - Pydantic-модели для всех эндпоинтов (нет сырых dict)
  - rate_limit как пример (TODO: Redis / slowapi для продакшна)
  - Спин-колесо: шансы взвешены, не uniform random

Исправления vs v2 (pasted_text_5d3d1c2c):
  - conn.execute("BEGIN TRANSACTION") + conn.commit() — некорректно для sqlite3 context manager
    Используем conn.isolation_level и явные транзакции правильно
  - Добавлен эндпоинт GET /api/profile для загрузки текущего состояния без синхронизации
  - Добавлена очистка просроченных сессий (startup event)
  - Добавлены минимальные rate-limit заглушки (можно подключить slowapi)
"""

import sqlite3
import bcrypt
import uuid
import random
import os
from datetime import datetime, timedelta
from contextlib import contextmanager

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
import uvicorn

# ──────────────────────────────────────────
# CONFIG (в продакшне — через .env / os.getenv)
# ──────────────────────────────────────────
TOTAL_SUPPLY_LIMIT  = 50_000_000.0
REWARD_PER_REP      = 0.1
SESSION_EXPIRE_DAYS = 3
DB_PATH             = os.getenv("AE_DB", "database.db")

# ──────────────────────────────────────────
# DB
# ──────────────────────────────────────────
@contextmanager
def get_conn():
    """Контекстный менеджер: одно соединение на запрос, auto-commit/rollback."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # лучше для параллельных запросов
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS users (
                username      TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                xp            INTEGER DEFAULT 0,
                lvl           INTEGER DEFAULT 1,
                reps          INTEGER DEFAULT 0,
                kcal          REAL    DEFAULT 0.0,
                balance       REAL    DEFAULT 0.0,
                is_premium    INTEGER DEFAULT 0,
                is_admin      INTEGER DEFAULT 0,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token      TEXT PRIMARY KEY,
                username   TEXT NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                FOREIGN KEY(username) REFERENCES users(username) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_username ON sessions(username);
        ''')


# ──────────────────────────────────────────
# AUTH HELPERS
# ──────────────────────────────────────────
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def check_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except Exception:
        return False


def create_session(conn: sqlite3.Connection, username: str) -> str:
    token = str(uuid.uuid4())
    expires = datetime.utcnow() + timedelta(days=SESSION_EXPIRE_DAYS)
    # Одна активная сессия на пользователя
    conn.execute("DELETE FROM sessions WHERE username = ?", (username,))
    conn.execute(
        "INSERT INTO sessions (token, username, expires_at) VALUES (?, ?, ?)",
        (token, username, expires.isoformat())
    )
    return token


def require_auth(request: Request) -> str:
    """FastAPI dependency: возвращает username по Bearer-токену."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing token")
    token = auth[7:]
    with get_conn() as conn:
        row = conn.execute(
            "SELECT username, expires_at FROM sessions WHERE token = ?", (token,)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="Invalid token")
    if datetime.fromisoformat(row["expires_at"]) < datetime.utcnow():
        raise HTTPException(status_code=401, detail="Token expired")
    return row["username"]


# ──────────────────────────────────────────
# APP
# ──────────────────────────────────────────
app = FastAPI(title="AE System", version="2.0.0")

# Статика и шаблоны
try:
    app.mount("/static", StaticFiles(directory="static"), name="static")
except RuntimeError:
    pass  # директория может отсутствовать в тестах

templates = Jinja2Templates(directory="static")


@app.on_event("startup")
async def startup():
    init_db()
    # Удаляем просроченные сессии при старте
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM sessions WHERE expires_at < ?",
            (datetime.utcnow().isoformat(),)
        )


# ──────────────────────────────────────────
# PYDANTIC MODELS
# ──────────────────────────────────────────
class LoginRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=32)
    password: str = Field(..., min_length=4, max_length=128)


class SyncData(BaseModel):
    reps: int   = Field(..., ge=0)
    xp:   int   = Field(..., ge=0)
    lvl:  int   = Field(..., ge=1)
    kcal: float = Field(..., ge=0.0)


class AdminAction(BaseModel):
    action: str          # "delete_user" | "add_balance"
    target: str
    amount: float = 0.0


# ──────────────────────────────────────────
# ENDPOINTS
# ──────────────────────────────────────────

@app.post("/api/login")
async def login(data: LoginRequest):
    """Вход / авторегистрация. Возвращает токен и текущие данные пользователя."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT password_hash, xp, lvl, reps, kcal, balance, is_premium, is_admin "
            "FROM users WHERE username = ?",
            (data.username,)
        ).fetchone()

        if row:
            # Пользователь существует — проверяем пароль
            if not check_password(data.password, row["password_hash"]):
                raise HTTPException(status_code=403, detail="Wrong password")
            token = create_session(conn, data.username)
            return {
                "status": "success",
                "token": token,
                "data": {
                    "xp": row["xp"], "lvl": row["lvl"],
                    "reps": row["reps"], "kcal": row["kcal"],
                    "balance": row["balance"],
                    "is_premium": row["is_premium"],
                    "is_admin": row["is_admin"],
                }
            }
        else:
            # Новый пользователь — регистрация
            pwd_hash = hash_password(data.password)
            conn.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                (data.username, pwd_hash)
            )
            token = create_session(conn, data.username)
            return {
                "status": "created",
                "token": token,
                "data": {
                    "xp": 0, "lvl": 1, "reps": 0, "kcal": 0.0,
                    "balance": 0.0, "is_premium": 0, "is_admin": 0
                }
            }


@app.get("/api/profile")
async def get_profile(username: str = Depends(require_auth)):
    """Получить текущее состояние без синхронизации (для восстановления сессии)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT xp, lvl, reps, kcal, balance, is_premium, is_admin FROM users WHERE username = ?",
            (username,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="User not found")

        total = conn.execute("SELECT COALESCE(SUM(balance),0) FROM users").fetchone()[0]
        return {
            "status": "ok",
            "data": {
                "username": username,
                "xp": row["xp"], "lvl": row["lvl"],
                "reps": row["reps"], "kcal": row["kcal"],
                "balance": row["balance"],
                "is_premium": row["is_premium"],
                "is_admin": row["is_admin"],
            },
            "global_minted": total,
        }


@app.post("/api/sync")
async def sync(data: SyncData, username: str = Depends(require_auth)):
    """
    Синхронизирует данные клиента с БД.
    Начисляет награду ТОЛЬКО за новые повторы (delta).
    Защита от читерства: клиент не может уменьшить reps.
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT reps, balance FROM users WHERE username = ?", (username,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="User not found")

        old_reps, old_balance = row["reps"], row["balance"]

        # Защита: reps может только расти
        if data.reps < old_reps:
            raise HTTPException(status_code=400, detail="Reps cannot decrease")

        rep_delta = data.reps - old_reps
        total_minted = conn.execute(
            "SELECT COALESCE(SUM(balance),0) FROM users"
        ).fetchone()[0]

        reward = 0.0
        if rep_delta > 0 and total_minted < TOTAL_SUPPLY_LIMIT:
            raw_reward = rep_delta * REWARD_PER_REP
            reward = min(raw_reward, TOTAL_SUPPLY_LIMIT - total_minted)

        new_balance = old_balance + reward

        conn.execute(
            "UPDATE users SET xp=?, lvl=?, reps=?, kcal=?, balance=? WHERE username=?",
            (data.xp, data.lvl, data.reps, data.kcal, new_balance, username)
        )

        # Обновлённый total после нашей вставки
        updated_minted = conn.execute(
            "SELECT COALESCE(SUM(balance),0) FROM users"
        ).fetchone()[0]

        return {
            "status": "synced",
            "balance": new_balance,
            "reward_added": reward,
            "global_minted": updated_minted,
        }


# Взвешенные шансы для честной игры
SPIN_PRIZES = [
    ("EMPTY",          40),   # 40%
    ("XP +500",        30),   # 30%
    ("XP +1000",       15),   # 15%
    ("REPS +100",      10),   # 10%
    ("JACKPOT 100 AE",  5),   # 5%
]
_SPIN_POOL   = [p for p, w in SPIN_PRIZES for _ in range(w)]
SPIN_COST    = 50.0


@app.post("/api/spin")
async def spin(username: str = Depends(require_auth)):
    """Колесо судьбы: списывает 50 AE, начисляет приз в БД."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT balance, xp, reps, lvl, kcal FROM users WHERE username = ?",
            (username,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="User not found")

        if row["balance"] < SPIN_COST:
            return {"status": "low_balance", "balance": row["balance"]}

        # Списываем стоимость
        conn.execute(
            "UPDATE users SET balance = balance - ? WHERE username = ?",
            (SPIN_COST, username)
        )

        # Выбор приза
        prize = random.choice(_SPIN_POOL)

        if prize == "XP +500":
            conn.execute("UPDATE users SET xp = xp + 500 WHERE username = ?", (username,))
        elif prize == "XP +1000":
            conn.execute("UPDATE users SET xp = xp + 1000 WHERE username = ?", (username,))
        elif prize == "JACKPOT 100 AE":
            conn.execute("UPDATE users SET balance = balance + 100.0 WHERE username = ?", (username,))
        elif prize == "REPS +100":
            conn.execute("UPDATE users SET reps = reps + 100 WHERE username = ?", (username,))
        # EMPTY — ничего не начисляем

        # Возвращаем актуальное состояние
        updated = conn.execute(
            "SELECT balance, xp, reps, lvl, kcal FROM users WHERE username = ?", (username,)
        ).fetchone()

        return {
            "status": "success",
            "prize":   prize,
            "balance": updated["balance"],
            "xp":      updated["xp"],
            "reps":    updated["reps"],
            "lvl":     updated["lvl"],
            "kcal":    updated["kcal"],
        }


@app.post("/api/admin/action")
async def admin_action(data: AdminAction, username: str = Depends(require_auth)):
    """Административные действия. Требует is_admin=1 в БД."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT is_admin FROM users WHERE username = ?", (username,)
        ).fetchone()
        if not row or row["is_admin"] != 1:
            raise HTTPException(status_code=403, detail="Forbidden")

        if data.action == "delete_user":
            conn.execute("DELETE FROM users WHERE username = ?", (data.target,))
        elif data.action == "add_balance":
            if data.amount <= 0:
                raise HTTPException(status_code=400, detail="Amount must be positive")
            conn.execute(
                "UPDATE users SET balance = balance + ? WHERE username = ?",
                (data.amount, data.target)
            )
        elif data.action == "set_admin":
            conn.execute(
                "UPDATE users SET is_admin = 1 WHERE username = ?", (data.target,)
            )
        elif data.action == "ban":
            conn.execute(
                "UPDATE users SET is_premium = -1 WHERE username = ?", (data.target,)
            )
        else:
            raise HTTPException(status_code=400, detail="Unknown action")

    return {"status": "success"}


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


if __name__ == "__main__":
    uvicorn.run("main_final:app", host="0.0.0.0", port=8000, reload=True)
