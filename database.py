import aiosqlite
import math
import random
from logger import logger

DB_PATH = "database.db"

# -----------------------
# Initialize the database
# -----------------------
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
    logger.info("Database initialized!")

# -----------------------
# Add user if not exists
# -----------------------
async def add_user(user_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO users(user_id) VALUES(?)", (user_id,))
        await db.commit()
    logger.debug(f"User added or already exists: {user_id}")

# -----------------------
# Random aura for level-up
# -----------------------
def random_aura_for_level(level: int) -> int:
    if 1 <= level <= 10:
        return random.randint(1, 100)
    elif 11 <= level <= 20:
        return random.randint(101, 300)
    elif 21 <= level <= 30:
        return random.randint(301, 500)
    else:
        return random.randint(501, 1000)

# -----------------------
# Update XP, messages, level, and aura
# -----------------------
async def update_user(user_id: str, xp_gain: int = 10):
    async with aiosqlite.connect(DB_PATH) as db:
        # Update XP and messages
        await db.execute("""
            UPDATE users
            SET xp = xp + ?, messages = messages + 1
            WHERE user_id = ?
        """, (xp_gain, user_id))

        # Fetch current stats
        cursor = await db.execute("SELECT xp, level, aura FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if row:
            xp, level, aura = row
            # Calculate new level
            new_level = int(math.sqrt(xp // 10)) + 1
            # If leveled up, give random aura
            if new_level > level:
                gained_aura = random_aura_for_level(new_level)
                aura += gained_aura
                logger.info(f"User {user_id} leveled up from {level} to {new_level}, gained {gained_aura} aura.")

            # Update level and aura
            await db.execute("UPDATE users SET level = ?, aura = ? WHERE user_id = ?", (new_level, aura, user_id))
            logger.debug(f"Updated user {user_id}: level={new_level}, xp={xp}, aura={aura}")

        await db.commit()

# -----------------------
# Get user info
# -----------------------
async def get_user(user_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        user = await cursor.fetchone()
    logger.debug(f"Fetched user {user_id}: {user}")
    return user

# -----------------------
# Modify aura directly
# -----------------------
async def modify_aura(user_id: str, amount: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT aura FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if not row:
            return False
        new_aura = max(row[0] + amount, 0)  # No negative aura
        await db.execute("UPDATE users SET aura = ? WHERE user_id = ?", (new_aura, user_id))
        await db.commit()
    logger.debug(f"Modified aura for {user_id}: {amount} -> new aura={new_aura}")
    return True
