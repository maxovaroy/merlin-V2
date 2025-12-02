import aiosqlite
import math

DB_PATH = "database.db"

# Connect to DB and create table if not exists
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                xp INTEGER DEFAULT 0,
                level INTEGER DEFAULT 1,
                messages INTEGER DEFAULT 0,
                aura INTEGER DEFAULT 0
            )
        """)
        await db.commit()

# Add user if not exists
async def add_user(user_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR IGNORE INTO users(user_id) VALUES(?)
        """, (user_id,))
        await db.commit()

# Update user XP, messages, and aura
async def update_user(user_id: str, xp_gain: int = 10):
    async with aiosqlite.connect(DB_PATH) as db:
        # Add XP and messages
        await db.execute("""
            UPDATE users
            SET xp = xp + ?,
                messages = messages + 1
            WHERE user_id = ?
        """, (xp_gain, user_id))

        # Get updated XP and messages
        cursor = await db.execute("SELECT xp, level, messages FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if row:
            xp, level, messages = row
            # Calculate new level
            new_level = int(math.sqrt(xp // 10)) + 1  # Simple XP -> Level formula
            aura = messages // 5  # 1 aura per 5 messages

            # Update level and aura
            await db.execute("""
                UPDATE users
                SET level = ?,
                    aura = ?
                WHERE user_id = ?
            """, (new_level, aura, user_id))

        await db.commit()

# Get user info
async def get_user(user_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        return await cursor.fetchone()
