import aiosqlite
import math
import random
from logger import logger

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
                aura INTEGER DEFAULT 0,
                aura_pool INTEGER DEFAULT 0
            )
        """)
        await db.commit()
    logger.info("Database initialized!")

# Add user if not exists
async def add_user(user_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR IGNORE INTO users(user_id) VALUES(?)
        """, (user_id,))
        await db.commit()
    logger.debug(f"User added or already exists: {user_id}")

# Update user XP, messages, aura, and aura_pool
async def update_user(user_id: str, xp_gain: int = 10):
    async with aiosqlite.connect(DB_PATH) as db:
        # Add XP and messages
        await db.execute("""
            UPDATE users
            SET xp = xp + ?,
                messages = messages + 1
            WHERE user_id = ?
        """, (xp_gain, user_id))

        # Get updated XP, level, messages, aura, and aura_pool
        cursor = await db.execute("SELECT xp, level, messages, aura_pool FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if row:
            xp, level, messages, aura_pool = row
            # Calculate new level
            new_level = int(math.sqrt(xp // 10)) + 1
            aura = messages // 5  # Aura from messages

            # Check if user leveled up
            if new_level > level:
                # Add random aura_pool points based on level
                if new_level <= 10:
                    gained_aura = random.randint(1, 100)
                elif 11 <= new_level <= 20:
                    gained_aura = random.randint(101, 300)
                elif 21 <= new_level <= 30:
                    gained_aura = random.randint(301, 500)
                else:
                    gained_aura = random.randint(501, 1000)
                aura_pool += gained_aura
                logger.info(f"User {user_id} leveled up to {new_level}! Gained {gained_aura} aura points in pool.")

            # Update level, aura, and aura_pool
            await db.execute("""
                UPDATE users
                SET level = ?,
                    aura = ?,
                    aura_pool = ?
                WHERE user_id = ?
            """, (new_level, aura, aura_pool, user_id))
            logger.debug(f"Updated user {user_id}: level={new_level}, aura={aura}, messages={messages}, xp={xp}, aura_pool={aura_pool}")

        await db.commit()

# Get user info
async def get_user(user_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        user = await cursor.fetchone()
    logger.debug(f"Fetched user {user_id}: {user}")
    return user

# Update aura pool when used on others
async def modify_aura_pool(user_id: str, amount: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT aura_pool FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if not row:
            return 0
        aura_pool = max(row[0] - amount, 0)  # prevent negative pool
        await db.execute("UPDATE users SET aura_pool = ? WHERE user_id = ?", (aura_pool, user_id))
        await db.commit()
        logger.debug(f"User {user_id} aura_pool modified by -{amount}, new pool: {aura_pool}")
        return aura_pool
