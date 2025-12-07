import aiosqlite
import math
import random
import time
from logger import logger

DB_PATH = "database.db"

# ============================================================
# DATABASE INITIALIZATION + MIGRATION CHECK
# ============================================================
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        # Create main table if missing
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                xp INTEGER DEFAULT 0,
                level INTEGER DEFAULT 1,
                messages INTEGER DEFAULT 0,
                aura INTEGER DEFAULT 0,
                streak_count INTEGER DEFAULT 0,
                last_streak_claim INTEGER DEFAULT 0
            )
        """)

        # Migrate if old installs do not have new streak columns
        await db.execute("ALTER TABLE users ADD COLUMN streak_count INTEGER DEFAULT 0") \
            if not await column_exists(db, "users", "streak_count") else None

        await db.execute("ALTER TABLE users ADD COLUMN last_streak_claim INTEGER DEFAULT 0") \
            if not await column_exists(db, "users", "last_streak_claim") else None

        await db.commit()

    logger.info("Database initialized & migrated successfully.")


async def column_exists(db, table, column):
    cursor = await db.execute(f"PRAGMA table_info({table})")
    columns = [row[1] for row in await cursor.fetchall()]
    return column in columns


# ============================================================
# USER MANAGEMENT
# ============================================================
async def add_user(user_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO users(user_id) VALUES(?)", (user_id,))
        await db.commit()
    logger.debug(f"User added/existed: {user_id}")


async def get_user(user_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        return await cur.fetchone()


async def get_all_users():  # for leaderboard pagination
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT * FROM users ORDER BY level DESC, xp DESC")
        return await cur.fetchall()


# ============================================================
# XP + LEVEL SYSTEM
# ============================================================
async def modify_aura(user_id: str, amount: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT aura FROM users WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        if not row:
            return False

        new_aura = max(row[0] + amount, 0)
        await db.execute("UPDATE users SET aura=? WHERE user_id=?", (new_aura, user_id))
        await db.commit()

    logger.debug(f"Aura updated for {user_id}: {amount:+} → {new_aura}")
    return True


async def update_user(user_id: str, xp_gain: int = 10):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE users
            SET xp = xp + ?, messages = messages + 1
            WHERE user_id = ?
        """, (xp_gain, user_id))

        cur = await db.execute("SELECT xp, level, aura FROM users WHERE user_id=?", (user_id,))
        row = await cur.fetchone()

        if row:
            xp, level, aura = row
            new_level = int(math.sqrt(xp // 10)) + 1

            if new_level > level:
                gained = random_aura_for_level(new_level)
                aura += gained
                logger.info(f"[LEVEL-UP] {user_id}: L{level} → L{new_level} (+{gained} aura)")

            await db.execute("UPDATE users SET level=?, aura=? WHERE user_id=?",
                             (new_level, aura, user_id))

        await db.commit()


def random_aura_for_level(level: int):
    if level <= 10: return random.randint(1, 100)
    if level <= 20: return random.randint(101, 300)
    if level <= 30: return random.randint(301, 500)
    return random.randint(501, 1000)


# ============================================================
# DAILY STREAK SYSTEM
# ============================================================
async def claim_daily(user_id: str, base_reward_xp=50, base_reward_aura=50):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT streak_count, last_streak_claim FROM users WHERE user_id=?", (user_id,))
        row = await cur.fetchone()

        if not row:
            return None

        streak, last_claim = row
        now = int(time.time())

        # 24h reset logic
        if now - last_claim < 86400:
            return False, streak, 0, 0  # not ready

        # if claim after 2+ days → streak reset
        if last_claim != 0 and now - last_claim > 172800:
            streak = 1
        else:
            streak += 1

        # reward increases with streak
        xp_reward = int(base_reward_xp * (1 + 0.1 * (streak - 1)))
        aura_reward = int(base_reward_aura * (1 + 0.1 * (streak - 1)))

        await db.execute("""
            UPDATE users 
            SET streak_count=?, last_streak_claim=?, xp=xp+?, aura=aura+?
            WHERE user_id=?
        """, (streak, now, xp_reward, aura_reward, user_id))

        await db.commit()
        return True, streak, xp_reward, aura_reward
