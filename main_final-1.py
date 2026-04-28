import sqlite3
import bcrypt
import uuid
import os
from datetime import datetime
from fastapi import FastAPI, Form, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

DB_PATH = os.environ.get("DB_PATH", "ae_grid.db")
app = FastAPI(title="AE SYSTEM", version="2.0")

# ─────────────────────────────────────────
#  DATABASE
# ─────────────────────────────────────────

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT    UNIQUE NOT NULL,
            password_hash TEXT    NOT NULL,
            balance       REAL    DEFAULT 0.0,
            xp            INTEGER DEFAULT 0,
            lvl           INTEGER DEFAULT 1,
            reps          INTEGER DEFAULT 0,
            kcal          REAL    DEFAULT 0.0,
            is_admin      INTEGER DEFAULT 0,
            created_at    TEXT    DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS sessions (
            token      TEXT    PRIMARY KEY,
            user_id    INTEGER NOT NULL,
            created_at TEXT    DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS workouts (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            reps       INTEGER NOT NULL,
            kcal       REAL    NOT NULL,
            note       TEXT    DEFAULT '',
            xp_gained  INTEGER NOT NULL,
            created_at TEXT    DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
    """)
    conn.commit()
    conn.close()


# ─────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────

def calc_xp(reps: int, kcal: float) -> int:
    """XP formula: base from reps + bonus from kcal."""
    return max(1, int(reps * 2 + kcal * 1.5))


def calc_level(xp: int) -> int:
    """Level = floor(xp / 1000) + 1, max 99."""
    return min(99, xp // 1000 + 1)


def calc_balance(xp: int) -> float:
    """$AE balance = xp / 100."""
    return round(xp / 100, 2)


def get_user_by_token(conn, token: str):
    row = conn.execute(
        "SELECT u.* FROM users u JOIN sessions s ON s.user_id = u.id WHERE s.token = ?",
        (token,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="Токен недействителен — войдите снова")
    return row


def user_to_dict(u) -> dict:
    return {
        "id":       u["id"],
        "username": u["username"],
        "balance":  u["balance"],
        "xp":       u["xp"],
        "lvl":      u["lvl"],
        "reps":     u["reps"],
        "kcal":     u["kcal"],
    }


# ─────────────────────────────────────────
#  STARTUP
# ─────────────────────────────────────────

@app.on_event("startup")
async def startup():
    init_db()
    print(f"[AE] DB initialised at {DB_PATH}")


# ─────────────────────────────────────────
#  STATIC / FRONTEND
# ─────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def read_root():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="index.html not found")
    return FileResponse(path)


# ─────────────────────────────────────────
#  AUTH
# ─────────────────────────────────────────

@app.post("/api/register")
async def register(
    username: str = Form(..., min_length=3, max_length=24),
    password: str = Form(..., min_length=4),
):
    username = username.strip()
    if not username.replace("_", "").replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="Только буквы, цифры, _ и -")

    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (username, pw_hash)
        )
        conn.commit()
        return {"status": "success", "message": "Агент создан"}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Агент с таким именем уже существует")
    finally:
        conn.close()


@app.post("/api/login")
async def login(
    username: str = Form(...),
    password: str = Form(...),
):
    conn = get_conn()
    try:
        user = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username.strip(),)
        ).fetchone()

        if not user or not bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
            raise HTTPException(status_code=401, detail="Неверный идентификатор или ключ доступа")

        # Create session token
        token = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO sessions (token, user_id) VALUES (?, ?)",
            (token, user["id"])
        )
        conn.commit()

        return {
            "token":  token,
            "status": "success",
            "data":   user_to_dict(user),
        }
    finally:
        conn.close()


@app.post("/api/logout")
async def logout(token: str = Form(...)):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()
        return {"status": "ok"}
    finally:
        conn.close()


# ─────────────────────────────────────────
#  USER DATA
# ─────────────────────────────────────────

@app.get("/api/me")
async def get_me(token: str = Query(...)):
    conn = get_conn()
    try:
        user = get_user_by_token(conn, token)
        return user_to_dict(user)
    finally:
        conn.close()


# ─────────────────────────────────────────
#  WORKOUT
# ─────────────────────────────────────────

@app.post("/api/workout")
async def log_workout(
    token: str = Form(...),
    reps:  int   = Form(..., ge=1, le=10000),
    kcal:  float = Form(..., ge=0, le=10000),
    note:  str   = Form(""),
):
    conn = get_conn()
    try:
        user = get_user_by_token(conn, token)
        uid  = user["id"]

        xp_gained = calc_xp(reps, kcal)
        new_xp    = user["xp"] + xp_gained
        new_lvl   = calc_level(new_xp)
        new_reps  = user["reps"] + reps
        new_kcal  = round(user["kcal"] + kcal, 2)
        new_bal   = calc_balance(new_xp)

        conn.execute(
            """UPDATE users
               SET xp=?, lvl=?, reps=?, kcal=?, balance=?
               WHERE id=?""",
            (new_xp, new_lvl, new_reps, new_kcal, new_bal, uid)
        )
        conn.execute(
            """INSERT INTO workouts (user_id, reps, kcal, note, xp_gained)
               VALUES (?, ?, ?, ?, ?)""",
            (uid, reps, round(kcal, 2), note.strip()[:200], xp_gained)
        )
        conn.commit()

        return {
            "status":    "success",
            "xp_gained": xp_gained,
            "xp":        new_xp,
            "lvl":       new_lvl,
            "balance":   new_bal,
            "leveled_up": new_lvl > user["lvl"],
        }
    finally:
        conn.close()


@app.get("/api/workouts")
async def get_workouts(
    token: str = Query(...),
    limit: int = Query(50, ge=1, le=200),
):
    conn = get_conn()
    try:
        user = get_user_by_token(conn, token)
        rows = conn.execute(
            """SELECT * FROM workouts
               WHERE user_id = ?
               ORDER BY created_at DESC
               LIMIT ?""",
            (user["id"], limit)
        ).fetchall()

        return {
            "workouts": [dict(r) for r in rows]
        }
    finally:
        conn.close()


# ─────────────────────────────────────────
#  LEADERBOARD
# ─────────────────────────────────────────

@app.get("/api/leaderboard")
async def leaderboard(limit: int = Query(20, ge=1, le=100)):
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT username, xp, lvl, reps, kcal
               FROM users
               ORDER BY xp DESC
               LIMIT ?""",
            (limit,)
        ).fetchall()
        return {"leaderboard": [dict(r) for r in rows]}
    finally:
        conn.close()


# ─────────────────────────────────────────
#  RUN
# ─────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
