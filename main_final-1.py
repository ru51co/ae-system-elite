import sqlite3
import bcrypt
import uuid
import os
from datetime import datetime, timedelta
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from pydantic import BaseModel
import uvicorn

# Настройки
DB_PATH = "database.db"
app = FastAPI()

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE, password_hash TEXT, balance REAL DEFAULT 0, xp INTEGER DEFAULT 0, lvl INTEGER DEFAULT 1, reps INTEGER DEFAULT 0, kcal REAL DEFAULT 0.0, is_admin INTEGER DEFAULT 0)")
    conn.execute("CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, user_id INTEGER)")
    conn.commit()
    conn.close()

@app.on_event("startup")
async def startup():
    init_db()

@app.get("/")
async def read_root():
    # Точный путь для Render
    file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index_final.html")
    if os.path.exists(file_path):
        return FileResponse(file_path)
    return HTMLResponse("<h1>Error: index_final.html not found</h1>", status_code=404)

@app.post("/api/register")
async def register(username: str = Form(...), password: str = Form(...)):
    hash_pw = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", (username, hash_pw))
        conn.commit()
        return {"status": "success", "message": "User created"}
    except:
        raise HTTPException(status_code=400, detail="User exists")
    finally:
        conn.close()

@app.post("/api/login")
async def login(username: str = Form(...), password: str = Form(...)):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()
    
    if user and bcrypt.checkpw(password.encode(), user['password_hash'].encode()):
        token = str(uuid.uuid4())
        conn = sqlite3.connect(DB_PATH)
        conn.execute("INSERT INTO sessions (token, user_id) VALUES (?, ?)", (token, user['id']))
        conn.commit()
        conn.close()
        return {
            "token": token, 
            "status": "success",
            "data": {
                "username": user['username'], 
                "balance": user['balance'],
                "xp": user['xp'],
                "lvl": user['lvl'],
                "reps": user['reps'],
                "kcal": user['kcal'],
                "is_admin": user['is_admin']
            }
        }
    raise HTTPException(status_code=401, detail="Invalid credentials")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
