import aiosqlite
import math
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
        logger.debug(f"Added {xp_gain} XP and incremented messages for user: {user_id}")

        # Get updated XP and messages
        cursor = await db.execute("SELECT xp, level, messages FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if row:
            xp, level, messages = row
            # Calculate new level
            new_level = int(math.sqrt(xp // 10)) + 1
            aura = messages // 5

            # Update level and aura
            await db.execute("""
                UPDATE users
                SET level = ?, aura = ?
                WHERE user_id = ?
            """, (new_level, aura, user_id))
            logger.debug(f"Updated user {user_id}: level={new_level}, aura={aura}, messages={messages}, xp={xp}")

        await db.commit()

# Get user info
async def get_user(user_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        user = await cursor.fetchone()
    logger.debug(f"Fetched user {user_id}: {user}")
    return user

# Update aura pool for a user
async def update_aura_pool(user_id: str, amount: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE users
            SET aura_pool = aura_pool + ?
            WHERE user_id = ?
        """, (amount, user_id))
        await db.commit()
    logger.debug(f"Updated aura pool for {user_id} by {amount}")

# Set aura pool (used when initializing or deducting)
async def set_aura_pool(user_id: str, amount: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE users
            SET aura_pool = ?
            WHERE user_id = ?
        """, (amount, user_id))
        await db.commit()
    logger.debug(f"Set aura pool for {user_id} to {amount}")

# Get aura pool
async def get_aura_pool(user_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT aura_pool FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
    return row[0] if row else 0
