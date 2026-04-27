"""
╔══════════════════════════════════════════════════════╗
║   ASSASSIN ENERGY — AUTONOMOUS GRID BACKEND          ║
║   FastAPI + SQLite | REST API для карты розеток       ║
╚══════════════════════════════════════════════════════╝
"""

import sqlite3
import bcrypt
import uuid
import os
import math
from datetime import datetime
from typing import Optional, List
from fastapi import FastAPI, Form, HTTPException, Query, Body
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

# ──────────────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────────────
DB_PATH = os.environ.get("DB_PATH", "ae_grid.db")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = FastAPI(
    title="ASSASSIN ENERGY API",
    description="Autonomous Grid — Розетки, Шагомер, GPS-треки",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ──────────────────────────────────────────────────────
# DATABASE
# ──────────────────────────────────────────────────────
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_conn()
    conn.executescript("""
        -- ПОЛЬЗОВАТЕЛИ
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT    UNIQUE NOT NULL,
            password_hash TEXT    NOT NULL,
            xp            INTEGER DEFAULT 0,
            lvl           INTEGER DEFAULT 1,
            total_steps   INTEGER DEFAULT 0,
            total_dist_m  REAL    DEFAULT 0.0,
            total_kcal    REAL    DEFAULT 0.0,
            outlets_added INTEGER DEFAULT 0,
            is_admin      INTEGER DEFAULT 0,
            created_at    TEXT    DEFAULT (datetime('now'))
        );

        -- СЕССИИ
        CREATE TABLE IF NOT EXISTS sessions (
            token      TEXT    PRIMARY KEY,
            user_id    INTEGER NOT NULL,
            created_at TEXT    DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        -- РОЗЕТКИ
        CREATE TABLE IF NOT EXISTS outlets (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL,
            address     TEXT    NOT NULL,
            lat         REAL    NOT NULL,
            lng         REAL    NOT NULL,
            type        TEXT    NOT NULL DEFAULT 'free',   -- free | paid | busy
            power       TEXT    DEFAULT '220В',
            plug_type   TEXT    DEFAULT 'EU',
            slots       INTEGER DEFAULT 1,
            description TEXT    DEFAULT '',
            added_by    INTEGER,
            verified    INTEGER DEFAULT 0,
            active      INTEGER DEFAULT 1,
            created_at  TEXT    DEFAULT (datetime('now')),
            updated_at  TEXT    DEFAULT (datetime('now')),
            FOREIGN KEY (added_by) REFERENCES users(id) ON DELETE SET NULL
        );

        -- ОТЗЫВЫ / РЕПОРТЫ
        CREATE TABLE IF NOT EXISTS outlet_reports (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            outlet_id  INTEGER NOT NULL,
            user_id    INTEGER,
            status     TEXT    NOT NULL,   -- free | busy | broken | wrong_info
            note       TEXT    DEFAULT '',
            created_at TEXT    DEFAULT (datetime('now')),
            FOREIGN KEY (outlet_id) REFERENCES outlets(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
        );

        -- ИЗБРАННОЕ
        CREATE TABLE IF NOT EXISTS favorites (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            outlet_id  INTEGER NOT NULL,
            created_at TEXT    DEFAULT (datetime('now')),
            UNIQUE(user_id, outlet_id),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (outlet_id) REFERENCES outlets(id) ON DELETE CASCADE
        );

        -- СЕССИИ ШАГОМЕРА
        CREATE TABLE IF NOT EXISTS step_sessions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            steps       INTEGER DEFAULT 0,
            dist_m      REAL    DEFAULT 0.0,
            kcal        REAL    DEFAULT 0.0,
            duration_s  INTEGER DEFAULT 0,
            started_at  TEXT    DEFAULT (datetime('now')),
            ended_at    TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        -- GPS-ТРЕКИ (точки маршрута)
        CREATE TABLE IF NOT EXISTS gps_points (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  INTEGER NOT NULL,
            lat         REAL    NOT NULL,
            lng         REAL    NOT NULL,
            accuracy    REAL,
            recorded_at TEXT    DEFAULT (datetime('now')),
            FOREIGN KEY (session_id) REFERENCES step_sessions(id) ON DELETE CASCADE
        );

        -- НАЧАЛЬНЫЕ РОЗЕТКИ (сид)
        INSERT OR IGNORE INTO outlets (id,name,address,lat,lng,type,power,plug_type,slots,verified)
        VALUES
          (1,'Кофейня «Бункер»','ул. Центральная, 5',55.7510,37.6180,'free','220В','EU',4,1),
          (2,'Библиотека им. Ленина','пр. Мира, 14',55.7525,37.6205,'paid','220В','EU',8,1),
          (3,'ТЦ «Галактика»','ул. Победы, 33',55.7490,37.6155,'free','220В','EU+USB',12,1),
          (4,'Коворкинг PULSE','ул. Новая, 7',55.7535,37.6220,'paid','220В','EU',20,1),
          (5,'Метро «Арсенал»','пл. Революции, 1',55.7500,37.6170,'busy','220В','EU',2,1),
          (6,'Парк «Восход»','Набережная, 1',55.7480,37.6140,'free','5V USB','USB',3,1),
          (7,'VIP-зал аэропорта','Шоссе аэропорта, 1',55.7550,37.6250,'paid','220В','Universal',30,1),
          (8,'Университет STEM','ул. Академика, 22',55.7470,37.6120,'free','220В','EU',6,1);
    """)
    conn.commit()
    conn.close()


@app.on_event("startup")
async def startup():
    init_db()
    print("⚡ ASSASSIN ENERGY BACKEND — ONLINE")


# ──────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────
def get_user_by_token(token: str) -> dict:
    conn = get_conn()
    row = conn.execute(
        "SELECT u.* FROM users u JOIN sessions s ON u.id=s.user_id WHERE s.token=?",
        (token,)
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=401, detail="Неверный или истёкший токен")
    return dict(row)


def haversine(lat1, lng1, lat2, lng2) -> float:
    """Расстояние между двумя координатами в метрах."""
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


def xp_for_action(action: str, **kw) -> int:
    table = {
        "add_outlet":   50,
        "report":       10,
        "step_session": lambda steps: min(steps // 100, 200),
        "favorite":     5,
    }
    v = table.get(action, 0)
    return v(**kw) if callable(v) else v


def update_user_xp(conn, user_id: int, xp_gained: int):
    row = conn.execute("SELECT xp FROM users WHERE id=?", (user_id,)).fetchone()
    new_xp = row["xp"] + xp_gained
    new_lvl = 1 + new_xp // 500
    conn.execute(
        "UPDATE users SET xp=?, lvl=? WHERE id=?",
        (new_xp, new_lvl, user_id)
    )


# ──────────────────────────────────────────────────────
# FRONTEND SERVE
# ──────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def root():
    path = os.path.join(BASE_DIR, "index.html")
    if os.path.exists(path):
        return FileResponse(path)
    return HTMLResponse("<h1>⚡ AE Backend running. Frontend not found.</h1>")


# ──────────────────────────────────────────────────────
# AUTH
# ──────────────────────────────────────────────────────
@app.post("/api/register", tags=["auth"])
async def register(
    username: str = Form(..., min_length=3, max_length=32),
    password: str = Form(..., min_length=6),
):
    """Регистрация нового агента."""
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    try:
        conn = get_conn()
        conn.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (username.strip(), hashed)
        )
        conn.commit()
        user_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()
        return {"status": "ok", "message": "Агент создан.", "user_id": user_id}
    except sqlite3.IntegrityError:
        raise HTTPException(400, "Имя пользователя уже занято")


@app.post("/api/login", tags=["auth"])
async def login(
    username: str = Form(...),
    password: str = Form(...),
):
    """Вход. Возвращает токен сессии."""
    conn = get_conn()
    user = conn.execute(
        "SELECT * FROM users WHERE username=?", (username.strip(),)
    ).fetchone()
    conn.close()
    if not user or not bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
        raise HTTPException(401, "Неверные данные")
    token = str(uuid.uuid4())
    conn = get_conn()
    conn.execute("INSERT INTO sessions (token, user_id) VALUES (?,?)", (token, user["id"]))
    conn.commit()
    conn.close()
    return {
        "status": "ok",
        "token": token,
        "agent": _user_public(dict(user)),
    }


@app.post("/api/logout", tags=["auth"])
async def logout(token: str = Form(...)):
    """Завершить сессию."""
    conn = get_conn()
    conn.execute("DELETE FROM sessions WHERE token=?", (token,))
    conn.commit()
    conn.close()
    return {"status": "ok"}


@app.get("/api/me", tags=["auth"])
async def me(token: str = Query(...)):
    """Профиль текущего агента."""
    return _user_public(get_user_by_token(token))


def _user_public(u: dict) -> dict:
    return {
        "id":           u["id"],
        "username":     u["username"],
        "xp":           u["xp"],
        "lvl":          u["lvl"],
        "total_steps":  u["total_steps"],
        "total_dist_m": u["total_dist_m"],
        "total_kcal":   u["total_kcal"],
        "outlets_added":u["outlets_added"],
        "is_admin":     bool(u["is_admin"]),
        "created_at":   u["created_at"],
    }


# ──────────────────────────────────────────────────────
# OUTLETS — РОЗЕТКИ
# ──────────────────────────────────────────────────────
class OutletCreate(BaseModel):
    name:        str
    address:     str
    lat:         float
    lng:         float
    type:        str = "free"      # free | paid | busy
    power:       str = "220В"
    plug_type:   str = "EU"
    slots:       int = 1
    description: str = ""


@app.get("/api/outlets", tags=["outlets"])
async def get_outlets(
    lat:     Optional[float] = Query(None),
    lng:     Optional[float] = Query(None),
    radius:  float  = Query(5000, description="Радиус поиска в метрах"),
    type:    Optional[str]   = Query(None, description="free|paid|busy"),
    limit:   int    = Query(100),
    token:   Optional[str]   = Query(None),
):
    """
    Получить список розеток.
    Если передать lat/lng — вернёт только те, что в радиусе radius метров,
    отсортированные по расстоянию.
    """
    conn = get_conn()
    query = "SELECT * FROM outlets WHERE active=1"
    params: list = []

    if type and type in ("free", "paid", "busy"):
        query += " AND type=?"
        params.append(type)

    rows = conn.execute(query, params).fetchall()

    # Получить избранное юзера
    fav_ids = set()
    if token:
        try:
            u = get_user_by_token(token)
            favs = conn.execute(
                "SELECT outlet_id FROM favorites WHERE user_id=?", (u["id"],)
            ).fetchall()
            fav_ids = {f["outlet_id"] for f in favs}
        except Exception:
            pass

    conn.close()

    result = []
    for r in rows:
        d = haversine(lat, lng, r["lat"], r["lng"]) if lat and lng else None
        if d is not None and d > radius:
            continue
        item = dict(r)
        item["distance_m"] = round(d) if d is not None else None
        item["is_favorite"] = r["id"] in fav_ids
        result.append(item)

    if lat and lng:
        result.sort(key=lambda x: x["distance_m"] or 0)

    return {"outlets": result[:limit], "total": len(result)}


@app.get("/api/outlets/{outlet_id}", tags=["outlets"])
async def get_outlet(outlet_id: int, token: Optional[str] = Query(None)):
    """Детальная информация о розетке."""
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM outlets WHERE id=? AND active=1", (outlet_id,)
    ).fetchone()
    if not row:
        raise HTTPException(404, "Розетка не найдена")

    reports = conn.execute(
        "SELECT r.*, u.username FROM outlet_reports r "
        "LEFT JOIN users u ON r.user_id=u.id "
        "WHERE r.outlet_id=? ORDER BY r.created_at DESC LIMIT 10",
        (outlet_id,)
    ).fetchall()

    fav = False
    if token:
        try:
            u = get_user_by_token(token)
            fav = bool(conn.execute(
                "SELECT 1 FROM favorites WHERE user_id=? AND outlet_id=?",
                (u["id"], outlet_id)
            ).fetchone())
        except Exception:
            pass

    conn.close()
    return {
        "outlet": dict(row),
        "reports": [dict(r) for r in reports],
        "is_favorite": fav,
    }


@app.post("/api/outlets", tags=["outlets"])
async def add_outlet(data: OutletCreate, token: str = Query(...)):
    """Добавить новую розетку. Требует авторизации."""
    user = get_user_by_token(token)

    if data.type not in ("free", "paid", "busy"):
        raise HTTPException(400, "type должен быть: free | paid | busy")
    if not (-90 <= data.lat <= 90) or not (-180 <= data.lng <= 180):
        raise HTTPException(400, "Некорректные координаты")

    conn = get_conn()
    conn.execute(
        """INSERT INTO outlets
           (name,address,lat,lng,type,power,plug_type,slots,description,added_by)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (data.name, data.address, data.lat, data.lng, data.type,
         data.power, data.plug_type, data.slots, data.description, user["id"])
    )
    outlet_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "UPDATE users SET outlets_added=outlets_added+1 WHERE id=?", (user["id"],)
    )
    xp = xp_for_action("add_outlet")
    update_user_xp(conn, user["id"], xp)
    conn.commit()
    conn.close()

    return {"status": "ok", "outlet_id": outlet_id, "xp_gained": xp}


@app.put("/api/outlets/{outlet_id}", tags=["outlets"])
async def update_outlet(outlet_id: int, data: OutletCreate, token: str = Query(...)):
    """Обновить розетку (только добавивший или admin)."""
    user = get_user_by_token(token)
    conn = get_conn()
    row = conn.execute(
        "SELECT added_by FROM outlets WHERE id=? AND active=1", (outlet_id,)
    ).fetchone()
    if not row:
        raise HTTPException(404, "Розетка не найдена")
    if row["added_by"] != user["id"] and not user["is_admin"]:
        raise HTTPException(403, "Нет прав на редактирование")

    conn.execute(
        """UPDATE outlets SET name=?,address=?,lat=?,lng=?,type=?,
           power=?,plug_type=?,slots=?,description=?,updated_at=datetime('now')
           WHERE id=?""",
        (data.name, data.address, data.lat, data.lng, data.type,
         data.power, data.plug_type, data.slots, data.description, outlet_id)
    )
    conn.commit()
    conn.close()
    return {"status": "ok"}


@app.delete("/api/outlets/{outlet_id}", tags=["outlets"])
async def delete_outlet(outlet_id: int, token: str = Query(...)):
    """Удалить (деактивировать) розетку."""
    user = get_user_by_token(token)
    conn = get_conn()
    row = conn.execute(
        "SELECT added_by FROM outlets WHERE id=?", (outlet_id,)
    ).fetchone()
    if not row:
        raise HTTPException(404, "Розетка не найдена")
    if row["added_by"] != user["id"] and not user["is_admin"]:
        raise HTTPException(403, "Нет прав")
    conn.execute("UPDATE outlets SET active=0 WHERE id=?", (outlet_id,))
    conn.commit()
    conn.close()
    return {"status": "ok"}


# ─ Report ─
class ReportCreate(BaseModel):
    status: str   # free | busy | broken | wrong_info
    note:   str = ""


@app.post("/api/outlets/{outlet_id}/report", tags=["outlets"])
async def report_outlet(outlet_id: int, data: ReportCreate, token: str = Query(...)):
    """Сообщить о статусе розетки."""
    user = get_user_by_token(token)
    if data.status not in ("free", "busy", "broken", "wrong_info"):
        raise HTTPException(400, "Неверный статус")

    conn = get_conn()
    conn.execute(
        "INSERT INTO outlet_reports (outlet_id,user_id,status,note) VALUES (?,?,?,?)",
        (outlet_id, user["id"], data.status, data.note)
    )
    # Если busy/broken — обновляем тип
    if data.status in ("busy", "broken"):
        conn.execute(
            "UPDATE outlets SET type='busy', updated_at=datetime('now') WHERE id=?",
            (outlet_id,)
        )
    elif data.status == "free":
        conn.execute(
            "UPDATE outlets SET type='free', updated_at=datetime('now') WHERE id=?",
            (outlet_id,)
        )

    xp = xp_for_action("report")
    update_user_xp(conn, user["id"], xp)
    conn.commit()
    conn.close()
    return {"status": "ok", "xp_gained": xp}


# ─ Favorites ─
@app.post("/api/outlets/{outlet_id}/favorite", tags=["outlets"])
async def toggle_favorite(outlet_id: int, token: str = Query(...)):
    """Добавить/убрать из избранного."""
    user = get_user_by_token(token)
    conn = get_conn()
    existing = conn.execute(
        "SELECT id FROM favorites WHERE user_id=? AND outlet_id=?",
        (user["id"], outlet_id)
    ).fetchone()
    if existing:
        conn.execute(
            "DELETE FROM favorites WHERE user_id=? AND outlet_id=?",
            (user["id"], outlet_id)
        )
        action = "removed"
    else:
        conn.execute(
            "INSERT INTO favorites (user_id, outlet_id) VALUES (?,?)",
            (user["id"], outlet_id)
        )
        update_user_xp(conn, user["id"], xp_for_action("favorite"))
        action = "added"
    conn.commit()
    conn.close()
    return {"status": "ok", "action": action}


@app.get("/api/favorites", tags=["outlets"])
async def get_favorites(token: str = Query(...)):
    """Избранные розетки пользователя."""
    user = get_user_by_token(token)
    conn = get_conn()
    rows = conn.execute(
        """SELECT o.* FROM outlets o
           JOIN favorites f ON o.id=f.outlet_id
           WHERE f.user_id=? AND o.active=1
           ORDER BY f.created_at DESC""",
        (user["id"],)
    ).fetchall()
    conn.close()
    return {"favorites": [dict(r) for r in rows]}


# ─ Nearest ─
@app.get("/api/outlets/nearest", tags=["outlets"])
async def nearest_outlet(
    lat:    float = Query(...),
    lng:    float = Query(...),
    type:   Optional[str] = Query(None),
    count:  int   = Query(5),
):
    """Найти ближайшие розетки к координатам."""
    conn = get_conn()
    q = "SELECT * FROM outlets WHERE active=1"
    p: list = []
    if type and type in ("free","paid"):
        q += " AND type=?"
        p.append(type)
    rows = conn.execute(q, p).fetchall()
    conn.close()

    with_dist = []
    for r in rows:
        d = haversine(lat, lng, r["lat"], r["lng"])
        item = dict(r)
        item["distance_m"] = round(d)
        with_dist.append(item)

    with_dist.sort(key=lambda x: x["distance_m"])
    return {"nearest": with_dist[:count]}


# ──────────────────────────────────────────────────────
# PEDOMETER — ШАГОМЕР
# ──────────────────────────────────────────────────────
class StepSessionStart(BaseModel):
    started_at: Optional[str] = None


class StepSessionEnd(BaseModel):
    steps:      int
    dist_m:     float
    kcal:       float
    duration_s: int
    ended_at:   Optional[str] = None


@app.post("/api/steps/start", tags=["pedometer"])
async def start_step_session(token: str = Query(...), data: StepSessionStart = Body(default=StepSessionStart())):
    """Начать сессию шагомера."""
    user = get_user_by_token(token)
    conn = get_conn()
    started = data.started_at or datetime.utcnow().isoformat()
    conn.execute(
        "INSERT INTO step_sessions (user_id, started_at) VALUES (?,?)",
        (user["id"], started)
    )
    session_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()
    return {"status": "ok", "session_id": session_id}


@app.post("/api/steps/{session_id}/end", tags=["pedometer"])
async def end_step_session(session_id: int, data: StepSessionEnd, token: str = Query(...)):
    """Завершить сессию шагомера и сохранить результаты."""
    user = get_user_by_token(token)
    conn = get_conn()

    row = conn.execute(
        "SELECT * FROM step_sessions WHERE id=? AND user_id=?",
        (session_id, user["id"])
    ).fetchone()
    if not row:
        raise HTTPException(404, "Сессия не найдена")

    ended = data.ended_at or datetime.utcnow().isoformat()
    conn.execute(
        """UPDATE step_sessions
           SET steps=?,dist_m=?,kcal=?,duration_s=?,ended_at=?
           WHERE id=?""",
        (data.steps, data.dist_m, round(data.kcal,2), data.duration_s, ended, session_id)
    )
    conn.execute(
        """UPDATE users SET
           total_steps=total_steps+?,
           total_dist_m=total_dist_m+?,
           total_kcal=total_kcal+?
           WHERE id=?""",
        (data.steps, data.dist_m, data.kcal, user["id"])
    )
    xp = xp_for_action("step_session", steps=data.steps)
    update_user_xp(conn, user["id"], xp)
    conn.commit()
    conn.close()
    return {"status": "ok", "xp_gained": xp}


@app.get("/api/steps/history", tags=["pedometer"])
async def step_history(token: str = Query(...), limit: int = Query(30)):
    """История сессий шагомера."""
    user = get_user_by_token(token)
    conn = get_conn()
    rows = conn.execute(
        """SELECT * FROM step_sessions WHERE user_id=? AND ended_at IS NOT NULL
           ORDER BY started_at DESC LIMIT ?""",
        (user["id"], limit)
    ).fetchall()
    conn.close()
    return {"sessions": [dict(r) for r in rows]}


@app.get("/api/steps/today", tags=["pedometer"])
async def steps_today(token: str = Query(...)):
    """Шаги за сегодня."""
    user = get_user_by_token(token)
    today = datetime.utcnow().strftime("%Y-%m-%d")
    conn = get_conn()
    row = conn.execute(
        """SELECT COALESCE(SUM(steps),0) as steps,
                  COALESCE(SUM(dist_m),0) as dist_m,
                  COALESCE(SUM(kcal),0)   as kcal
           FROM step_sessions
           WHERE user_id=? AND date(started_at)=?""",
        (user["id"], today)
    ).fetchone()
    conn.close()
    return {
        "date": today,
        "steps": row["steps"],
        "dist_m": row["dist_m"],
        "kcal":   row["kcal"],
    }


# ──────────────────────────────────────────────────────
# GPS TRACKS — GPS-ТРЕКИ
# ──────────────────────────────────────────────────────
class GPSPoint(BaseModel):
    lat:         float
    lng:         float
    accuracy:    Optional[float] = None
    recorded_at: Optional[str]   = None


@app.post("/api/gps/{session_id}/point", tags=["gps"])
async def add_gps_point(session_id: int, point: GPSPoint, token: str = Query(...)):
    """Добавить GPS-точку к треку сессии."""
    user = get_user_by_token(token)
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM step_sessions WHERE id=? AND user_id=?",
        (session_id, user["id"])
    ).fetchone()
    if not row:
        raise HTTPException(404, "Сессия не найдена")
    ts = point.recorded_at or datetime.utcnow().isoformat()
    conn.execute(
        "INSERT INTO gps_points (session_id,lat,lng,accuracy,recorded_at) VALUES (?,?,?,?,?)",
        (session_id, point.lat, point.lng, point.accuracy, ts)
    )
    conn.commit()
    conn.close()
    return {"status": "ok"}


@app.post("/api/gps/{session_id}/batch", tags=["gps"])
async def add_gps_batch(session_id: int, points: List[GPSPoint], token: str = Query(...)):
    """Пакетная загрузка GPS-точек."""
    user = get_user_by_token(token)
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM step_sessions WHERE id=? AND user_id=?",
        (session_id, user["id"])
    ).fetchone()
    if not row:
        raise HTTPException(404, "Сессия не найдена")
    for p in points:
        ts = p.recorded_at or datetime.utcnow().isoformat()
        conn.execute(
            "INSERT INTO gps_points (session_id,lat,lng,accuracy,recorded_at) VALUES (?,?,?,?,?)",
            (session_id, p.lat, p.lng, p.accuracy, ts)
        )
    conn.commit()
    conn.close()
    return {"status": "ok", "saved": len(points)}


@app.get("/api/gps/{session_id}/track", tags=["gps"])
async def get_gps_track(session_id: int, token: str = Query(...)):
    """Получить GPS-трек сессии."""
    user = get_user_by_token(token)
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM step_sessions WHERE id=? AND user_id=?",
        (session_id, user["id"])
    ).fetchone()
    if not row:
        raise HTTPException(404, "Сессия не найдена")
    points = conn.execute(
        "SELECT lat,lng,accuracy,recorded_at FROM gps_points WHERE session_id=? ORDER BY recorded_at",
        (session_id,)
    ).fetchall()
    conn.close()
    return {
        "session": dict(row),
        "points":  [dict(p) for p in points],
        "count":   len(points),
    }


# ──────────────────────────────────────────────────────
# LEADERBOARD — РЕЙТИНГ
# ──────────────────────────────────────────────────────
@app.get("/api/leaderboard", tags=["social"])
async def leaderboard(
    by:    str = Query("xp", description="xp | steps | dist | outlets"),
    limit: int = Query(20),
):
    """Топ агентов."""
    column_map = {
        "xp":      "xp",
        "steps":   "total_steps",
        "dist":    "total_dist_m",
        "outlets": "outlets_added",
    }
    col = column_map.get(by, "xp")
    conn = get_conn()
    rows = conn.execute(
        f"SELECT username,xp,lvl,total_steps,total_dist_m,outlets_added "
        f"FROM users ORDER BY {col} DESC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    return {
        "category": by,
        "leaderboard": [
            {**dict(r), "rank": i+1}
            for i, r in enumerate(rows)
        ]
    }


# ──────────────────────────────────────────────────────
# ADMIN
# ──────────────────────────────────────────────────────
def require_admin(token: str) -> dict:
    user = get_user_by_token(token)
    if not user["is_admin"]:
        raise HTTPException(403, "Требуются права администратора")
    return user


@app.get("/api/admin/users", tags=["admin"])
async def admin_users(token: str = Query(...)):
    """Список всех пользователей (admin only)."""
    require_admin(token)
    conn = get_conn()
    rows = conn.execute(
        "SELECT id,username,xp,lvl,total_steps,outlets_added,is_admin,created_at FROM users ORDER BY id"
    ).fetchall()
    conn.close()
    return {"users": [dict(r) for r in rows]}


@app.post("/api/admin/outlets/{outlet_id}/verify", tags=["admin"])
async def verify_outlet(outlet_id: int, token: str = Query(...)):
    """Верифицировать розетку (admin only)."""
    require_admin(token)
    conn = get_conn()
    conn.execute("UPDATE outlets SET verified=1 WHERE id=?", (outlet_id,))
    conn.commit()
    conn.close()
    return {"status": "ok"}


@app.get("/api/admin/stats", tags=["admin"])
async def admin_stats(token: str = Query(...)):
    """Общая статистика системы."""
    require_admin(token)
    conn = get_conn()
    stats = {}
    stats["users"]          = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    stats["outlets_total"]  = conn.execute("SELECT COUNT(*) FROM outlets WHERE active=1").fetchone()[0]
    stats["outlets_free"]   = conn.execute("SELECT COUNT(*) FROM outlets WHERE type='free' AND active=1").fetchone()[0]
    stats["outlets_paid"]   = conn.execute("SELECT COUNT(*) FROM outlets WHERE type='paid' AND active=1").fetchone()[0]
    stats["outlets_busy"]   = conn.execute("SELECT COUNT(*) FROM outlets WHERE type='busy' AND active=1").fetchone()[0]
    stats["step_sessions"]  = conn.execute("SELECT COUNT(*) FROM step_sessions WHERE ended_at IS NOT NULL").fetchone()[0]
    stats["total_steps"]    = conn.execute("SELECT COALESCE(SUM(total_steps),0) FROM users").fetchone()[0]
    stats["total_gps_pts"]  = conn.execute("SELECT COUNT(*) FROM gps_points").fetchone()[0]
    stats["reports"]        = conn.execute("SELECT COUNT(*) FROM outlet_reports").fetchone()[0]
    conn.close()
    return {"stats": stats}


# ──────────────────────────────────────────────────────
# HEALTHCHECK
# ──────────────────────────────────────────────────────
@app.get("/health", tags=["system"])
async def health():
    return {"status": "ok", "service": "ASSASSIN ENERGY", "version": "2.0.0"}


# ──────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
