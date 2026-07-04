import sqlite3
import logging

DB_FILE = 'database.db'
logger = logging.getLogger("TradingBot.DB")

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    # 1. Credentials table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS credentials (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        consumer_key TEXT,
        consumer_secret TEXT,
        mobile_number TEXT,
        ucc TEXT,
        mpin TEXT,
        totp_secret TEXT
    )
    """)
    
    # 2. Trades table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT,
        action TEXT,
        entry_price REAL,
        exit_price REAL,
        quantity INTEGER,
        exit_reason TEXT,
        pnl REAL,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    # 3. Settings table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        virtual_balance REAL DEFAULT 100000.0,
        trailing_sl_pct REAL DEFAULT 1.0,
        target_pct REAL DEFAULT 2.0,
        nifty_qty INTEGER DEFAULT 65,
        banknifty_qty INTEGER DEFAULT 30,
        is_active INTEGER DEFAULT 0,
        sl_hits_count INTEGER DEFAULT 0,
        max_daily_sl INTEGER DEFAULT 3,
        implied_volatility REAL DEFAULT 0.165,
        expiry_date TEXT DEFAULT '2026-06-30'
    )
    """)
    
    # Seed default settings if empty
    cursor.execute("SELECT COUNT(*) FROM settings")
    if cursor.fetchone()[0] == 0:
        cursor.execute("""
        INSERT INTO settings (
            virtual_balance, trailing_sl_pct, target_pct, nifty_qty, banknifty_qty, 
            is_active, sl_hits_count, max_daily_sl, implied_volatility, expiry_date
        ) VALUES (
            100000.0, 1.0, 2.0, 65, 30, 0, 0, 3, 0.165, '2026-06-30'
        )
        """)
        conn.commit()
    else:
        # Run standard migrations to ensure correct options columns exist
        migrations = [
            ("nifty_qty", "ALTER TABLE settings ADD COLUMN nifty_qty INTEGER DEFAULT 65"),
            ("banknifty_qty", "ALTER TABLE settings ADD COLUMN banknifty_qty INTEGER DEFAULT 30"),
            ("max_daily_sl", "ALTER TABLE settings ADD COLUMN max_daily_sl INTEGER DEFAULT 3"),
            ("implied_volatility", "ALTER TABLE settings ADD COLUMN implied_volatility REAL DEFAULT 0.165"),
            ("expiry_date", "ALTER TABLE settings ADD COLUMN expiry_date TEXT DEFAULT '2026-06-30'")
        ]
        for col, sql in migrations:
            try:
                cursor.execute(f"SELECT {col} FROM settings LIMIT 1")
            except sqlite3.OperationalError:
                cursor.execute(sql)
                conn.commit()
                
    conn.close()
    logger.info("SQLite Database initialized and seeded successfully for Option Trading Console.")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db()
