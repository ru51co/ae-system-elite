# Замени функции работы с БД на зависимости (DI)
from fastapi import Depends

def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    try:
        yield db
    finally:
        db.close()

# Оптимизированный поиск розеток
@app.get("/api/outlets", tags=["outlets"])
async def get_outlets(
    lat: Optional[float] = Query(None),
    lng: Optional[float] = Query(None),
    radius: float = Query(5000),
    token: Optional[str] = Query(None),
    db: sqlite3.Connection = Depends(get_db) # Используем зависимость
):
    params = []
    sql = "SELECT * FROM outlets WHERE active=1"
    
    # Гео-фильтрация на уровне SQL (Bounding Box)
    if lat is not None and lng is not None:
        # Примерно 1 градус ~ 111км. Считаем границы квадрата для отсечения лишнего.
        lat_delta = radius / 111000
        lng_delta = radius / (111000 * math.cos(math.radians(lat)))
        
        sql += " AND lat BETWEEN ? AND ? AND lng BETWEEN ? AND ?"
        params.extend([lat - lat_delta, lat + lat_delta, lng - lng_delta, lng + lng_delta])

    rows = db.execute(sql, params).fetchall()
    
    # Дальнейшая точная фильтрация через Haversine (уже в памяти, но на малом наборе)
    result = []
    for r in rows:
        dist = haversine(lat, lng, r["lat"], r["lng"]) if lat and lng else None
        if dist is not None and dist > radius:
            continue
        # ... формирование ответа
