import aiosqlite
import math
import random
from logger import logger  # Logger for debug and info messages

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

# Calculate new level from XP
def calculate_level(xp):
    return int(math.sqrt(xp // 10)) + 1

# Determine aura reward when leveling up
def get_aura_reward(level):
    if level <= 10:
        return random.randint(1, 100)
    elif 11 <= level <= 20:
        return random.randint(101, 300)
    elif 21 <= level <= 30:
        return random.randint(301, 500)
    elif 31 <= level <= 40:
        return random.randint(501, 700)
    else:
        return random.randint(701, 1000)

# Update user XP, messages, aura, and aura pool
async def update_user(user_id: str, xp_gain: int = 10):
    async with aiosqlite.connect(DB_PATH) as db:
        # Add XP and increment messages
        await db.execute("""
            UPDATE users
            SET xp = xp + ?,
                messages = messages + 1
            WHERE user_id = ?
        """, (xp_gain, user_id))

        # Get updated user info
        cursor = await db.execute("SELECT xp, level, messages, aura, aura_pool FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if row:
            xp, level, messages, aura, aura_pool = row
            new_level = calculate_level(xp)

            # Give aura pool reward if leveled up
            if new_level > level:
                reward = get_aura_reward(new_level)
                aura_pool += reward
                logger.info(f"User {user_id} leveled up from {level} to {new_level}! "
                            f"Aura pool increased by {reward} to {aura_pool}.")

            # Update level, aura, and aura pool
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

# Spend aura from aura pool
async def spend_aura(user_id: str, amount: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT aura_pool FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if not row:
            logger.warning(f"Attempted to spend aura for non-existent user {user_id}")
            return False

        aura_pool = row[0]
        if amount > aura_pool:
            amount = aura_pool  # Limit the maximum spend to available aura
        aura_pool -= amount

        await db.execute("UPDATE users SET aura_pool = aura_pool - ? WHERE user_id = ?", (amount, user_id))
        await db.commit()
        logger.debug(f"User {user_id} spent {amount} aura from aura pool. Remaining: {aura_pool}")
        return amount
