import sqlite3
import bcrypt
import uuid
import os
from datetime import datetime, timedelta
from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
import uvicorn

# Настройки
DB_PATH = "database.db"

app = FastAPI()

# База данных
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE, password_hash TEXT, balance REAL DEFAULT 0)")
    conn.execute("CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, user_id INTEGER)")
    conn.commit()
    conn.close()

@app.on_event("startup")
async def startup():
    init_db()

# Главная страница
@app.get("/")
async def read_root():
    # Находим путь к файлу в текущей папке
    file_path = os.path.join(os.getcwd(), "index_final.html")
    if os.path.exists(file_path):
        return FileResponse(file_path)
    return {"error": "HTML file not found in root"}

# API: Регистрация
@app.post("/api/register")
async def register(username: str = Form(...), password: str = Form(...)):
    hash_pw = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", (username, hash_pw))
        conn.commit()
        return {"status": "success"}
    except:
        raise HTTPException(status_code=400, detail="User exists")
    finally:
        conn.close()

# API: Логин
@app.post("/api/login")
async def login(username: str = Form(...), password: str = Form(...)):
    conn = sqlite3.connect(DB_PATH)
    user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()
    if user and bcrypt.checkpw(password.encode(), user[2].encode()):
        token = str(uuid.uuid4())
        return {"token": token, "username": username, "balance": user[3]}
    raise HTTPException(status_code=401)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
    
