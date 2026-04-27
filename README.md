# 🦅 AE System | Elite Order

Цифровая экосистема для тренировок с защищенной архитектурой и лимитированной эмиссией $AE. 

## 🛠 Технологии
- **Backend:** FastAPI (Python 3.9+)
- **Database:** SQLite3 (WAL mode)
- **Security:** Bcrypt (hashing), UUID4 (session management)
- **Frontend:** Vanilla JS, HTML5 (Neon UI Design)

## 💎 Особенности (Elite Version)
- **Ultra-Security:** Пароли защищены алгоритмом `bcrypt` с солью. Это стандарт индустрии, который невозможно взломать простым перебором.
- **Smart Sync:** Начисление валюты $AE происходит через расчет разницы (delta) на сервере. Нельзя "нарисовать" баланс, изменив цифру в браузере.
- **Session Control:** Авторизация через случайные токены UUID4. Безопасный доступ без передачи пароля в открытом виде.
- **Emission Limit:** Жестко прописанный лимит в 50,000,000 $AE.
