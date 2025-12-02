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

# Calculate random aura based on level
def random_aura_for_level(level: int) -> int:
    if 1 <= level <= 10:
        return random.randint(1, 100)
    elif 11 <= level <= 20:
        return random.randint(101, 300)
    elif 21 <= level <= 30:
        return random.randint(301, 500)
    else:
        return random.randint(501, 1000)

# Update user XP, messages, level, aura, and aura_pool
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

        # Get updated stats
        cursor = await db.execute("SELECT xp, level, messages, aura, aura_pool FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if row:
            xp, level, messages, aura, aura_pool = row
            # Calculate new level
            new_level = int(math.sqrt(xp // 10)) + 1  # Simple XP -> Level formula
            # If user leveled up, give them random aura to their pool
            if new_level > level:
                gained_aura = random_aura_for_level(new_level)
                aura_pool += gained_aura
                logger.info(f"User {user_id} leveled up from {level} to {new_level} and gained {gained_aura} aura in pool.")

            # Update level and aura
            await db.execute("""
                UPDATE users
                SET level = ?, aura = ?, aura_pool = ?
                WHERE user_id = ?
            """, (new_level, aura, aura_pool, user_id))
            logger.debug(f"Updated user {user_id}: level={new_level}, aura={aura}, aura_pool={aura_pool}, messages={messages}, xp={xp}")

        await db.commit()

# Get user info
async def get_user(user_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        user = await cursor.fetchone()
    logger.debug(f"Fetched user {user_id}: {user}")
    return user

# Modify aura of another user (from aura command)
async def modify_aura(user_id: str, amount: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT aura, aura_pool FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if not row:
            return False
        aura, aura_pool = row
        new_aura = max(0, min(aura + amount, 100))  # Max 100 when taking aura
        await db.execute("UPDATE users SET aura = ?, aura_pool = ? WHERE user_id = ?", (new_aura, aura_pool, user_id))
        await db.commit()
    logger.debug(f"Modified aura for {user_id}: {amount} -> new aura={new_aura}")
    return True

# Spend aura from pool
async def spend_aura(user_id: str, amount: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT aura_pool FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if not row or row[0] < amount:
            return False
        new_pool = row[0] - amount
        await db.execute("UPDATE users SET aura_pool = ? WHERE user_id = ?", (new_pool, user_id))
        await db.commit()
    logger.debug(f"User {user_id} spent {amount} aura from pool. Remaining pool: {new_pool}")
    return True
