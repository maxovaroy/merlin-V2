import sqlite3
import threading

class Database:
    def __init__(self):
        self.lock = threading.Lock()
        self.conn = sqlite3.connect("merlin.db", check_same_thread=False)
        self.cur = self.conn.cursor()
        self.setup()

    def setup(self):
        self.cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                xp INTEGER DEFAULT 0,
                level INTEGER DEFAULT 1,
                messages INTEGER DEFAULT 0,
                aura INTEGER DEFAULT 0
            )
        """)
        self.conn.commit()

    def ensure_user(self, uid):
        self.cur.execute("""
            INSERT OR IGNORE INTO users (user_id) VALUES (?)
        """, (uid,))
        self.conn.commit()

    def add_message(self, uid):
        with self.lock:
            self.ensure_user(uid)
            self.cur.execute("""
                UPDATE users SET messages = messages + 1, xp = xp + 5
                WHERE user_id = ?
            """, (uid,))
            self.conn.commit()

    def get_user(self, uid):
        self.cur.execute("SELECT * FROM users WHERE user_id = ?", (uid,))
        return self.cur.fetchone()

db = Database()
