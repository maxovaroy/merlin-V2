import aiosqlite
import math
import random
from logger import logger  # Import the logger we created

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

# Update user XP, messages, level, aura, and aura pool
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
        cursor = await db.execute("SELECT xp, level, messages, aura_pool FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if row:
            xp, level, messages, aura_pool = row
            # Calculate new level
            new_level = int(math.sqrt(xp // 10)) + 1  # Simple XP -> Level formula

            # Calculate aura granted on level-up
            new_aura_points = 0
            if new_level > level:  # Only if leveled up
                if new_level <= 10:
                    new_aura_points = random.randint(1, 100)
                elif 11 <= new_level <= 20:
                    new_aura_points = random.randint(101, 300)
                elif 21 <= new_level <= 30:
                    new_aura_points = random.randint(301, 600)
                else:  # Level 31+
                    new_aura_points = random.randint(601, 1000)

                aura_pool += new_aura_points
                logger.info(f"User {user_id} leveled up from {level} to {new_level} and gained {new_aura_points} aura points!")

            aura = messages // 5  # 1 aura per 5 messages

            # Update level, aura, and aura pool
            await db.execute("""
                UPDATE users
                SET level = ?,
                    aura = ?,
                    aura_pool = ?
                WHERE user_id = ?
            """, (new_level, aura, aura_pool, user_id))

        await db.commit()
        logger.debug(f"Updated user {user_id}: level={new_level}, aura={aura}, messages={messages}, xp={xp}, aura_pool={aura_pool}")

# Get user info
async def get_user(user_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        user = await cursor.fetchone()
    logger.debug(f"Fetched user {user_id}: {user}")
    return user

# Get aura pool
async def get_aura_pool(user_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT aura_pool FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
    aura_pool = row[0] if row else 0
    logger.debug(f"Fetched aura pool for {user_id}: {aura_pool}")
    return aura_pool

# Spend aura on another user
async def spend_aura(from_user_id: str, to_user_id: str, amount: int):
    async with aiosqlite.connect(DB_PATH) as db:
        # Get sender's aura pool
        cursor = await db.execute("SELECT aura_pool FROM users WHERE user_id = ?", (from_user_id,))
        row = await cursor.fetchone()
        if not row:
            return False  # Sender not found
        current_pool = row[0]
        if amount > current_pool:
            amount = current_pool  # Cap at available pool

        # Deduct from sender's pool
        await db.execute("UPDATE users SET aura_pool = aura_pool - ? WHERE user_id = ?", (amount, from_user_id))
        # Add to receiver's aura
        await db.execute("UPDATE users SET aura = aura + ? WHERE user_id = ?", (amount, to_user_id))
        await db.commit()
    logger.info(f"User {from_user_id} spent {amount} aura on {to_user_id}")
    return True
