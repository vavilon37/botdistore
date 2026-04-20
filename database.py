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
                description TEXT,
                photo_id TEXT,
                active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS item_photos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL,
                photo_id TEXT NOT NULL,
                sort_order INTEGER DEFAULT 0,
                FOREIGN KEY (item_id) REFERENCES items(id)
            )
        """)
        await db.commit()


async def add_item(name: str, category: str, condition: str, price: int,
                   description: str, photo_id: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO items (name, category, condition, price, description, photo_id) VALUES (?, ?, ?, ?, ?, ?)",
            (name, category, condition, price, description, photo_id)
        )
        await db.commit()
        return cursor.lastrowid


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
                    min_price: int = None, max_price: int = None) -> list:
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

    query += " ORDER BY created_at DESC"

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, params) as cursor:
            return await cursor.fetchall()


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
