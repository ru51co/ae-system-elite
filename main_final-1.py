import sqlite3
import bcrypt
import uuid
import random
import os
from datetime import datetime, timedelta
from contextlib import contextmanager

from fastapi import FastAPI, Request, Form, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from pydantic import BaseModel, Field
import uvicorn

# --- CONFIG ---
TOTAL_SUPPLY_LIMIT = 50_000_000.0
REWARD_PER_REP = 0.1
SESSION_EXPIRE_DAYS = 3
DB_PATH = os.getenv("AE_DB", "database.db")

# --- DB ---
@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                balance REAL DEFAULT 0.0,
                is_admin INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)

app = FastAPI()

# Инициализация БД при запуске
@app.on_event("startup")
async def startup():
    init_db()

# --- MODELS ---
class RegisterForm(BaseModel):
    username: str
    password: str

class SyncData(BaseModel):
    token: str
    reps_delta: int

# --- API ENDPOINTS ---

@app.get("/", response_class=FileResponse)
async def read_root():
    # Просто отдаем файл из корня репозитория
    return FileResponse("index_final.html")

@app.post("/api/register")
async def register(username: str = Form(...), password: str = Form(...)):
    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                (username, pw_hash)
            )
        return {"status": "success", "message": "User created"}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Username taken")

@app.post("/api/login")
async def login(username: str = Form(...), password: str = Form(...)):
    with get_conn() as conn:
        user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if not user or not bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
            raise HTTPException(status_code=401, detail="Invalid credentials")
        
        token = str(uuid.uuid4())
        expires = datetime.now() + timedelta(days=SESSION_EXPIRE_DAYS)
        conn.execute(
            "INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
            (token, user["id"], expires)
        )
        return {"token": token, "username": user["username"], "balance": user["balance"]}

@app.post("/api/sync")
async def sync_balance(data: SyncData):
    with get_conn() as conn:
        session = conn.execute(
            "SELECT * FROM sessions WHERE token = ? AND expires_at > ?",
            (data.token, datetime.now())
        ).fetchone()
        
        if not session:
            raise HTTPException(status_code=401, detail="Session expired")
        
        reward = data.reps_delta * REWARD_PER_REP
        conn.execute(
            "UPDATE users SET balance = balance + ? WHERE id = ?",
            (reward, session["user_id"])
        )
        new_balance = conn.execute(
            "SELECT balance FROM users WHERE id = ?", (session["user_id"],)
        ).fetchone()["balance"]
        
        return {"new_balance": new_balance}

@app.get("/api/profile")
async def get_profile(token: str):
    with get_conn() as conn:
        user = conn.execute(
            "SELECT u.username, u.balance FROM users u "
            "JOIN sessions s ON u.id = s.user_id "
            "WHERE s.token = ? AND s.expires_at > ?",
            (token, datetime.now())
        ).fetchone()
        if not user:
            raise HTTPException(status_code=401, detail="Not found")
        return {"username": user["username"], "balance": user["balance"]}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
  
