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
        # Users table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                xp INTEGER DEFAULT 0,
                level INTEGER DEFAULT 1,
                messages INTEGER DEFAULT 0,
                aura INTEGER DEFAULT 0
            )
        """)

        # Skin reports/votes table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS skin_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                skin_name TEXT NOT NULL,
                user_id TEXT NOT NULL,
                UNIQUE(skin_name, user_id)
            )
        """)

        await db.commit()
    logger.info("Database initialized!")

# -----------------------
# User management
# -----------------------
async def add_user(user_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO users(user_id) VALUES(?)", (user_id,))
        await db.commit()
    logger.debug(f"User added or already exists: {user_id}")

async def get_user(user_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        user = await cursor.fetchone()
    logger.debug(f"Fetched user {user_id}: {user}")
    return user

async def modify_aura(user_id: str, amount: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT aura FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if not row:
            return False
        new_aura = max(row[0] + amount, 0)
        await db.execute("UPDATE users SET aura = ? WHERE user_id = ?", (new_aura, user_id))
        await db.commit()
    logger.debug(f"Modified aura for {user_id}: {amount} -> new aura={new_aura}")
    return True

def random_aura_for_level(level: int) -> int:
    if 1 <= level <= 10:
        return random.randint(1, 100)
    elif 11 <= level <= 20:
        return random.randint(101, 300)
    elif 21 <= level <= 30:
        return random.randint(301, 500)
    else:
        return random.randint(501, 1000)

async def update_user(user_id: str, xp_gain: int = 10):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE users
            SET xp = xp + ?, messages = messages + 1
            WHERE user_id = ?
        """, (xp_gain, user_id))

        cursor = await db.execute("SELECT xp, level, aura FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if row:
            xp, level, aura = row
            new_level = int(math.sqrt(xp // 10)) + 1
            if new_level > level:
                gained_aura = random_aura_for_level(new_level)
                aura += gained_aura
                logger.info(f"User {user_id} leveled up from {level} to {new_level}, gained {gained_aura} aura.")
            await db.execute("UPDATE users SET level = ?, aura = ? WHERE user_id = ?", (new_level, aura, user_id))
            logger.debug(f"Updated user {user_id}: level={new_level}, xp={xp}, aura={aura}")

        await db.commit()

# -----------------------
# Skin report / vote system
# -----------------------
async def add_skin_report(user_id: str, skin_name: str):
    """Add a skin report / suggestion for a user."""
    skin_name = skin_name.strip()
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute("INSERT INTO skin_reports(user_id, skin_name) VALUES(?, ?)", (user_id, skin_name))
            await db.commit()
            logger.debug(f"User {user_id} reported skin: {skin_name}")
            return True
        except aiosqlite.IntegrityError:
            return False  # already reported by this user

async def vote_skin(user_id: str, skin_name: str):
    """Vote for a skin (same as report, prevents duplicate votes)."""
    return await add_skin_report(user_id, skin_name)

async def get_top_reports(limit: int = 10):
    """Return top skins by vote count."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT skin_name, COUNT(*) as votes
            FROM skin_reports
            GROUP BY skin_name
            ORDER BY votes DESC
            LIMIT ?
        """, (limit,))
        rows = await cursor.fetchall()
        return rows

async def remove_skin_report(skin_name: str):
    """Remove a skin from reports/votes table."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM skin_reports WHERE skin_name = ?", (skin_name.strip(),))
        await db.commit()
    logger.debug(f"Removed skin report: {skin_name}")
