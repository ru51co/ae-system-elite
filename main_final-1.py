import sqlite3, hashlib, random, httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import os

app = FastAPI()

# Создаем папку для статики, если её нет
if not os.path.exists("static"):
    os.makedirs("static")

# Подключаем шаблоны (путь к папке со фронтендом)
templates = Jinja2Templates(directory="static")

TOTAL_SUPPLY_LIMIT = 50000000.0
REWARD_PER_REP = 0.1

def init_db():
    with sqlite3.connect('database.db') as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS users 
            (username TEXT PRIMARY KEY, password TEXT, xp INTEGER DEFAULT 0, 
             lvl INTEGER DEFAULT 1, reps INTEGER DEFAULT 0, kcal REAL DEFAULT 0.0,
             balance REAL DEFAULT 0.0, is_premium INTEGER DEFAULT 0)''')
        # Таблица для розеток (карты)
        conn.execute('''CREATE TABLE IF NOT EXISTS sockets 
            (id INTEGER PRIMARY KEY AUTOINCREMENT, lat REAL, lon REAL, desc TEXT)''')
init_db()

class SyncData(BaseModel):
    username: str
    reps: int
    xp: int
    lvl: int
    kcal: float

@app.post("/api/login")
async def login(data: dict):
    hashed = hashlib.sha256(data['password'].encode()).hexdigest()
    with sqlite3.connect('database.db') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT xp, lvl, reps, kcal, balance, is_premium FROM users WHERE username = ? AND password = ?", (data['username'], hashed))
        user = cursor.fetchone()
        if user:
            return {"status": "success", "data": {"xp": user[0], "lvl": user[1], "reps": user[2], "kcal": user[3], "balance": user[4], "is_premium": user[5]}}
        else:
            conn.execute("INSERT INTO users (username, password) VALUES (?, ?)", (data['username'], hashed))
            return {"status": "created"}

@app.post("/api/sync")
async def sync(data: SyncData):
    with sqlite3.connect('database.db') as conn:
        reward = round(data.reps * REWARD_PER_REP, 2)
        conn.execute('UPDATE users SET xp=?, lvl=?, reps=?, kcal=?, balance=? WHERE username=?',
                     (data.xp, data.lvl, data.reps, data.kcal, reward, data.username))
    return {"status": "synced"}

@app.get("/api/weather")
async def weather(lat: float, lon: float):
    # Заглушка (для реальных данных нужен API ключ OpenWeather)
    return {"temp": 24, "condition": "Clear"}

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
