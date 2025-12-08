# database.py
import aiosqlite
import math
import random
import time
import logging
from typing import Optional, Tuple, List, Any

logger = logging.getLogger("database")
if not logger.handlers:
    logger.addHandler(logging.StreamHandler())
logger.setLevel(logging.DEBUG)

DB_PATH = "database.db"

# -------------------------
# Low-level helpers
# -------------------------
async def _table_exists(db: aiosqlite.Connection, name: str) -> bool:
    cur = await db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return (await cur.fetchone()) is not None

async def _column_exists(db: aiosqlite.Connection, table: str, column: str) -> bool:
    cur = await db.execute(f"PRAGMA table_info({table})")
    rows = await cur.fetchall()
    cols = [r[1] for r in rows]
    return column in cols

# -------------------------
# Safe migration helper
# -------------------------
async def _ensure_users_columns(db: aiosqlite.Connection):
    """
    Ensure users table has final schema columns:
      user_id, xp, level, messages, aura, streak_count, last_streak_claim
    Attempts ALTER TABLE; on failure it will perform a safe copy migration.
    """
    # If users table doesn't exist, nothing to migrate here.
    if not await _table_exists(db, "users"):
        return

    needed = []
    for col, default in (("streak_count", "0"), ("last_streak_claim", "0")):
        if not await _column_exists(db, "users", col):
            needed.append((col, default))

    if not needed:
        return

    logger.info("DB migration: need to add columns %s", [c for c, _ in needed])

    # Try ALTER TABLE first
    try:
        for col, default in needed:
            await db.execute(f"ALTER TABLE users ADD COLUMN {col} INTEGER DEFAULT {default}")
        await db.commit()
        logger.info("DB migration via ALTER TABLE succeeded.")
        return
    except Exception as e:
        logger.warning("ALTER TABLE failed: %s — falling back to safe copy.", e)

    # Fallback: safe copy table method
    try:
        # Get current columns
        cur = await db.execute("PRAGMA table_info(users)")
        rows = await cur.fetchall()
        existing_cols = [r[1] for r in rows]
        logger.debug("Existing users columns: %s", existing_cols)

        # New final schema
        final_cols = [
            ("user_id", "TEXT PRIMARY KEY"),
            ("xp", "INTEGER DEFAULT 0"),
            ("level", "INTEGER DEFAULT 1"),
            ("messages", "INTEGER DEFAULT 0"),
            ("aura", "INTEGER DEFAULT 0"),
            ("streak_count", "INTEGER DEFAULT 0"),
            ("last_streak_claim", "INTEGER DEFAULT 0")
        ]

        # Create new table users_new
        cols_sql = ",\n    ".join(f"{c} {t}" for c, t in final_cols)
        await db.execute(f"CREATE TABLE IF NOT EXISTS users_new (\n    {cols_sql}\n)")

        # Build SELECT list: use existing columns where present, else literal default 0
        select_parts = []
        for c, _ in final_cols:
            if c in existing_cols:
                select_parts.append(c)
            else:
                select_parts.append("0 AS " + c)
        select_list = ", ".join(select_parts)
        insert_cols = ", ".join([c for c, _ in final_cols])

        await db.execute(f"INSERT INTO users_new ({insert_cols}) SELECT {select_list} FROM users")
        await db.execute("DROP TABLE users")
        await db.execute("ALTER TABLE users_new RENAME TO users")
        await db.commit()
        logger.info("Safe copy migration succeeded.")
    except Exception:
        logger.exception("Safe copy migration failed.")
        raise

# -------------------------
# Initialization
# -------------------------
async def init_db():
    """
    Create tables if missing and run migrations to ensure final schema.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        # Users (final schema)
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

        # skin_reports table (one report per user per skin) with timestamp
        await db.execute("""
            CREATE TABLE IF NOT EXISTS skin_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                skin_name TEXT NOT NULL,
                user_id TEXT NOT NULL,
                created_at INTEGER DEFAULT (strftime('%s','now')),
                UNIQUE(skin_name, user_id)
            )
        """)

        # skin_votes table (independent votes) with timestamp
        await db.execute("""
            CREATE TABLE IF NOT EXISTS skin_votes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                skin_name TEXT NOT NULL,
                user_id TEXT NOT NULL,
                created_at INTEGER DEFAULT (strftime('%s','now')),
                UNIQUE(skin_name, user_id)
            )
        """)

        await db.commit()
        # ensure older DBs have the additional columns, using safe migration helper
        await _ensure_users_columns(db)

    logger.info("Database initialized and migrated if necessary.")

# -------------------------
# User management helpers
# -------------------------
async def add_user(user_id: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO users(user_id) VALUES(?)", (user_id,))
        await db.commit()
    logger.debug("add_user(%s)", user_id)

async def get_user(user_id: str) -> Optional[aiosqlite.Row]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
    logger.debug("get_user(%s) -> %s", user_id, row)
    return row

async def get_all_users(limit: int = 1000) -> List[aiosqlite.Row]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT * FROM users ORDER BY level DESC, xp DESC LIMIT ?", (limit,))
        rows = await cur.fetchall()
    return rows

# -------------------------
# XP, level, aura logic
# -------------------------
def random_aura_for_level(level: int) -> int:
    if 1 <= level <= 10:
        return random.randint(1, 100)
    elif 11 <= level <= 20:
        return random.randint(101, 300)
    elif 21 <= level <= 30:
        return random.randint(301, 500)
    else:
        return random.randint(501, 1000)

async def modify_aura(user_id: str, amount: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT aura FROM users WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
        if not row:
            return False
        new_aura = max((row[0] or 0) + amount, 0)
        await db.execute("UPDATE users SET aura = ? WHERE user_id = ?", (new_aura, user_id))
        await db.commit()
    logger.debug("modify_aura(%s, %s) -> %s", user_id, amount, new_aura)
    return True

async def update_user(user_id: str, xp_gain: int = 10):
    """
    Increase xp by xp_gain and messages by 1.
    Recalculate level and add aura on level up.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO users(user_id) VALUES(?)", (user_id,))
        await db.execute("""
            UPDATE users
            SET xp = xp + ?, messages = messages + 1
            WHERE user_id = ?
        """, (xp_gain, user_id))

        cur = await db.execute("SELECT xp, level, aura FROM users WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
        if row:
            xp, level, aura = row
            new_level = int(math.sqrt(xp // 10)) + 1
            if new_level > level:
                gained_aura = random_aura_for_level(new_level)
                aura = (aura or 0) + gained_aura
                logger.info("update_user: level-up for %s %s -> %s (+%s aura)", user_id, level, new_level, gained_aura)
            await db.execute("UPDATE users SET level = ?, aura = ? WHERE user_id = ?", (new_level, aura, user_id))

        await db.commit()

# -------------------------
# Daily streak logic (24h window)
# -------------------------
async def claim_daily(user_id: str, base_reward_xp: int = 50, base_reward_aura: int = 50) -> Tuple[bool, int, int, int]:
    """
    Claims daily reward for `user_id`.
    Returns tuple: (success_flag, streak_count, xp_reward, aura_reward)
    If already claimed within last 24h returns (False, streak_count, 0, 0)
    """
    now = int(time.time())
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO users(user_id) VALUES(?)", (user_id,))
        cur = await db.execute("SELECT streak_count, last_streak_claim FROM users WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
        if not row:
            # shouldn't happen, but guard
            streak = 0
            last = 0
        else:
            streak = int(row[0] or 0)
            last = int(row[1] or 0)

        # Already claimed in last 24 hours?
        if now - last < 86400:
            return False, streak, 0, 0

        # If last claim > 48h ago -> reset streak
        if last != 0 and (now - last) > 172800:
            streak = 1
        else:
            streak = streak + 1

        # reward scaling: +10% per consecutive day (capped sensibly)
        multiplier = 1.0 + 0.10 * (streak - 1)
        if multiplier > 5.0:  # safety cap
            multiplier = 5.0

        xp_reward = int(base_reward_xp * multiplier)
        aura_reward = int(base_reward_aura * multiplier)

        await db.execute("""
            UPDATE users
            SET streak_count = ?, last_streak_claim = ?, xp = xp + ?, aura = aura + ?
            WHERE user_id = ?
        """, (streak, now, xp_reward, aura_reward, user_id))

        await db.commit()
        return True, streak, xp_reward, aura_reward

# -------------------------
# Skin reports & votes (Option 2 semantics)
# -------------------------
async def add_skin_report(user_id: str, skin_name: str) -> bool:
    """
    Add a report/suggestion. Returns True if added, False if user already reported that skin.
    """
    skin_name = skin_name.strip()
    now = int(time.time())
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute("INSERT INTO skin_reports (skin_name, user_id, created_at) VALUES (?, ?, ?)",
                             (skin_name, user_id, now))
            await db.commit()
            logger.debug("add_skin_report: %s by %s", skin_name, user_id)
            return True
        except aiosqlite.IntegrityError:
            return False

async def vote_skin(user_id: str, skin_name: str) -> bool:
    """
    Add a vote for a skin. Returns True if added, False if duplicate.
    """
    skin_name = skin_name.strip()
    now = int(time.time())
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute("INSERT INTO skin_votes (skin_name, user_id, created_at) VALUES (?, ?, ?)",
                             (skin_name, user_id, now))
            await db.commit()
            logger.debug("vote_skin: %s by %s", skin_name, user_id)
            return True
        except aiosqlite.IntegrityError:
            return False

async def remove_skin_report(skin_name: str) -> int:
    """
    Remove all reports for skin_name (string). Returns number of rows deleted.
    """
    skin_name = skin_name.strip()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("DELETE FROM skin_reports WHERE skin_name = ?", (skin_name,))
        await db.commit()
        deleted = cur.rowcount if hasattr(cur, "rowcount") else 0
    logger.debug("remove_skin_report: %s deleted=%s", skin_name, deleted)
    return deleted

async def remove_skin_vote(user_id: str, skin_name: str) -> bool:
    """
    Remove a single user's vote for a skin. Returns True if a row removed.
    """
    skin_name = skin_name.strip()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("DELETE FROM skin_votes WHERE skin_name = ? AND user_id = ?", (skin_name, user_id))
        await db.commit()
        # cur.rowcount may not be reliable in aiosqlite; fetch presence instead
        return True if cur.rowcount and cur.rowcount > 0 else False

async def get_top_reports(limit: int = 10) -> List[Tuple[str, int]]:
    """
    Return top skins by combined (reports + votes) count.
    Returns list of tuples: (skin_name, total_votes)
    """
    async with aiosqlite.connect(DB_PATH) as db:
        # Build a unified list of skin names from both tables, then aggregate counts
        query = """
        WITH names AS (
            SELECT skin_name FROM skin_reports
            UNION
            SELECT skin_name FROM skin_votes
        ),
        r AS (
            SELECT skin_name, COUNT(*) AS reports_count FROM skin_reports GROUP BY skin_name
        ),
        v AS (
            SELECT skin_name, COUNT(*) AS votes_count FROM skin_votes GROUP BY skin_name
        )
        SELECT names.skin_name,
               IFNULL(r.reports_count, 0) + IFNULL(v.votes_count, 0) AS total
        FROM names
        LEFT JOIN r ON r.skin_name = names.skin_name
        LEFT JOIN v ON v.skin_name = names.skin_name
        ORDER BY total DESC
        LIMIT ?
        """
        cur = await db.execute(query, (limit,))
        rows = await cur.fetchall()
    # rows are tuples (skin_name, total)
    return [(r[0], int(r[1])) for r in rows]

# -------------------------
# Utility: remove skin vote/report helper (admin)
# -------------------------
async def clear_all_skin_reports():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM skin_reports")
        await db.commit()
    logger.debug("clear_all_skin_reports executed")

async def clear_all_skin_votes():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM skin_votes")
        await db.commit()
    logger.debug("clear_all_skin_votes executed")
