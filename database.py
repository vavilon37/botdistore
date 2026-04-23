import aiosqlite
import os

DB_PATH = os.path.join(os.getenv("DATA_DIR", "."), "shop.db")


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                condition TEXT NOT NULL,
                price INTEGER NOT NULL,
                buy_price INTEGER DEFAULT 0,
                model TEXT,
                storage TEXT,
                color TEXT,
                description TEXT,
                photo_id TEXT,
                active INTEGER DEFAULT 1,
                sold_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Миграции для уже существующих БД — добавляем недостающие колонки
        for col, ddl in [
            ("buy_price",  "ALTER TABLE items ADD COLUMN buy_price INTEGER DEFAULT 0"),
            ("model",      "ALTER TABLE items ADD COLUMN model TEXT"),
            ("storage",    "ALTER TABLE items ADD COLUMN storage TEXT"),
            ("color",      "ALTER TABLE items ADD COLUMN color TEXT"),
            ("sold_at",    "ALTER TABLE items ADD COLUMN sold_at TIMESTAMP"),
        ]:
            try:
                await db.execute(ddl)
            except Exception:
                pass  # колонка уже есть
        await db.execute("""
            CREATE TABLE IF NOT EXISTS item_photos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL,
                photo_id TEXT NOT NULL,
                sort_order INTEGER DEFAULT 0,
                FOREIGN KEY (item_id) REFERENCES items(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS favorites (
                user_id INTEGER NOT NULL,
                item_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, item_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                user_id INTEGER NOT NULL,
                model TEXT NOT NULL,
                PRIMARY KEY (user_id, model)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS item_views (
                item_id INTEGER NOT NULL PRIMARY KEY,
                views INTEGER DEFAULT 0
            )
        """)
        await db.commit()


async def add_item(name: str, category: str, condition: str, price: int,
                   description: str, photo_id: str,
                   buy_price: int = 0, model: str = "",
                   storage: str = "", color: str = "") -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO items (name, category, condition, price, buy_price, "
            "model, storage, color, description, photo_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (name, category, condition, price, buy_price,
             model, storage, color, description, photo_id)
        )
        await db.commit()
        return cursor.lastrowid


async def mark_sold(item_id: int, sell_price: int = None):
    """Помечаем товар проданным: active=0, sold_at=now, опц. перезапись цены."""
    async with aiosqlite.connect(DB_PATH) as db:
        if sell_price is not None:
            await db.execute(
                "UPDATE items SET active = 0, sold_at = CURRENT_TIMESTAMP, price = ? WHERE id = ?",
                (sell_price, item_id)
            )
        else:
            await db.execute(
                "UPDATE items SET active = 0, sold_at = CURRENT_TIMESTAMP WHERE id = ?",
                (item_id,)
            )
        await db.commit()


async def add_item_photos(item_id: int, photo_ids: list[str]):
    async with aiosqlite.connect(DB_PATH) as db:
        for i, pid in enumerate(photo_ids):
            await db.execute(
                "INSERT INTO item_photos (item_id, photo_id, sort_order) VALUES (?, ?, ?)",
                (item_id, pid, i)
            )
        await db.commit()


async def get_item_photos(item_id: int) -> list[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT photo_id FROM item_photos WHERE item_id = ? ORDER BY sort_order",
            (item_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [r[0] for r in rows]


async def get_items(category: str = None, condition: str = None,
                    min_price: int = None, max_price: int = None,
                    sort: str = "date") -> list:
    query = "SELECT * FROM items WHERE active = 1"
    params = []

    if category:
        query += " AND category = ?"
        params.append(category)
    if condition:
        query += " AND condition = ?"
        params.append(condition)
    if min_price is not None:
        query += " AND price >= ?"
        params.append(min_price)
    if max_price is not None:
        query += " AND price <= ?"
        params.append(max_price)

    if sort == "price_asc":
        query += " ORDER BY price ASC"
    elif sort == "price_desc":
        query += " ORDER BY price DESC"
    elif sort == "name":
        query += " ORDER BY name ASC"
    else:
        query += " ORDER BY created_at DESC"

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, params) as cursor:
            return await cursor.fetchall()


async def get_price_range(category: str = None, min_price: int = None, max_price: int = None) -> tuple:
    query = "SELECT MIN(price), MAX(price) FROM items WHERE active = 1"
    params = []
    if category:
        query += " AND category = ?"
        params.append(category)
    if min_price is not None:
        query += " AND price >= ?"
        params.append(min_price)
    if max_price is not None:
        query += " AND price <= ?"
        params.append(max_price)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(query, params) as cursor:
            row = await cursor.fetchone()
            return (row[0] or 0, row[1] or 0)


async def search_items(query_text: str) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM items WHERE active = 1 AND (name LIKE ? OR description LIKE ?) ORDER BY created_at DESC",
            (f"%{query_text}%", f"%{query_text}%")
        ) as cursor:
            return await cursor.fetchall()


async def get_item(item_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM items WHERE id = ?", (item_id,)) as cursor:
            return await cursor.fetchone()


async def delete_item(item_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE items SET active = 0 WHERE id = ?", (item_id,))
        await db.commit()


async def get_categories() -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT DISTINCT category FROM items WHERE active = 1"
        ) as cursor:
            rows = await cursor.fetchall()
            return [r[0] for r in rows]


async def register_user(user_id: int, username: str, first_name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO users (user_id, username, first_name) VALUES (?, ?, ?)",
            (user_id, username or "", first_name or "")
        )
        await db.commit()


async def get_all_user_ids() -> list[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM users") as cursor:
            rows = await cursor.fetchall()
            return [r[0] for r in rows]


async def count_users() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0


async def count_active_items() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM items WHERE active = 1") as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0


async def count_used_items() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM items WHERE active = 1 AND condition != 'Новое'"
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0


# --- Избранное ---

async def add_favorite(user_id: int, item_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO favorites (user_id, item_id) VALUES (?, ?)",
            (user_id, item_id)
        )
        await db.commit()


async def remove_favorite(user_id: int, item_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM favorites WHERE user_id = ? AND item_id = ?",
            (user_id, item_id)
        )
        await db.commit()


async def is_favorite(user_id: int, item_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM favorites WHERE user_id = ? AND item_id = ?",
            (user_id, item_id)
        ) as cursor:
            return await cursor.fetchone() is not None


async def get_favorites(user_id: int) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT i.* FROM items i JOIN favorites f ON i.id = f.item_id "
            "WHERE f.user_id = ? AND i.active = 1 ORDER BY f.created_at DESC",
            (user_id,)
        ) as cursor:
            return await cursor.fetchall()


# --- Подписки на модели ---

async def add_subscription(user_id: int, model: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO subscriptions (user_id, model) VALUES (?, ?)",
            (user_id, model)
        )
        await db.commit()


async def remove_subscription(user_id: int, model: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM subscriptions WHERE user_id = ? AND model = ?",
            (user_id, model)
        )
        await db.commit()


async def is_subscribed(user_id: int, model: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM subscriptions WHERE user_id = ? AND model = ?", (user_id, model)
        ) as cursor:
            return await cursor.fetchone() is not None


async def get_subscribers(model: str) -> list[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_id FROM subscriptions WHERE model = ?", (model,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [r[0] for r in rows]


async def get_user_subscriptions(user_id: int) -> list[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT model FROM subscriptions WHERE user_id = ?", (user_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [r[0] for r in rows]


# --- Просмотры ---

async def increment_views(item_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO item_views (item_id, views) VALUES (?, 1) "
            "ON CONFLICT(item_id) DO UPDATE SET views = views + 1",
            (item_id,)
        )
        await db.commit()


async def get_views(item_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT views FROM item_views WHERE item_id = ?", (item_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0
