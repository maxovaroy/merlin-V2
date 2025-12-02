import aiosqlite
import asyncio

class Database:
    def __init__(self):
        self.db = None

    async def connect(self):
        self.db = await aiosqlite.connect("merlin.db")
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                xp INTEGER DEFAULT 0,
                level INTEGER DEFAULT 1,
                messages INTEGER DEFAULT 0,
                aura INTEGER DEFAULT 0
            )
        """)
        await self.db.commit()

    async def add_message(self, uid):
        await self.db.execute("""
            INSERT OR IGNORE INTO users (user_id) VALUES (?)
        """, (uid,))

        await self.db.execute("""
            UPDATE users SET messages = messages + 1, xp = xp + 5
            WHERE user_id = ?
        """, (uid,))

        await self.db.commit()

    async def get_user(self, uid):
        cursor = await self.db.execute(
            "SELECT * FROM users WHERE user_id = ?", (uid,)
        )
        return await cursor.fetchone()

db = Database()
