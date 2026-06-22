import sqlite3
import psycopg2
import os
from urllib.parse import urlparse

# ── CONFIG ──────────────────────────────────────────────────────────────────
LOCAL_DB = "instance/elimu.db"
SUPABASE_URL = os.environ.get("DATABASE_URL")
if not SUPABASE_URL:
    SUPABASE_URL = input("Enter your Supabase DATABASE_URL: ")
SUPABASE_URL = SUPABASE_URL.replace("postgres://", "postgresql://", 1)
if "sslmode" not in SUPABASE_URL:
    SUPABASE_URL += ("&" if "?" in SUPABASE_URL else "?") + "sslmode=require"

# ── CONNECT ─────────────────────────────────────────────────────────────────
print("Connecting to local SQLite...")
src = sqlite3.connect(LOCAL_DB)
src.row_factory = sqlite3.Row

print("Connecting to Supabase PostgreSQL...")
dst = psycopg2.connect(SUPABASE_URL)
dst.autocommit = False
cur = dst.cursor()

# ── TABLES IN ORDER (respecting FK dependencies) ────────────────────────────
TABLES = {
    "school": "school",
    "user": '"user"',
    "student": "student",
    "subject": "subject",
    "mark": "mark",
    "timetable": "timetable",
    "invoice": "invoice",
    "payment": "payment",
}

try:
    for name, quoted in TABLES.items():
        rows = src.execute(f'SELECT * FROM {name}').fetchall()
        if not rows:
            print(f"  {name}: 0 rows (skip)")
            continue
        col_names = [desc[0] for desc in src.execute(f'SELECT * FROM {name} LIMIT 0').description]
        placeholders = ", ".join(["%s"] * len(col_names))
        cols = ", ".join(col_names)
        insert_sql = f"INSERT INTO {quoted} ({cols}) VALUES ({placeholders})"
        for row in rows:
            vals = [row[c] for c in col_names]
            try:
                cur.execute(insert_sql, vals)
            except Exception as ex:
                print(f"  WARN {name}: skip row {dict(row)} — {ex}")
        dst.commit()
        print(f"  OK {name}: {len(rows)} rows migrated")

    print("\nMigration complete! Visit /health or /setup to verify.")
except Exception as e:
    dst.rollback()
    print(f"\nERROR: {e}")
finally:
    src.close()
    dst.close()
