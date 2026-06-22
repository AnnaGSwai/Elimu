import sqlite3
import psycopg2
import os

LOCAL_DB = "instance/elimu.db"
SUPABASE_URL = os.environ.get("DATABASE_URL")
if not SUPABASE_URL:
    SUPABASE_URL = input("Enter your DATABASE_URL: ")
SUPABASE_URL = SUPABASE_URL.replace("postgres://", "postgresql://", 1)
if "sslmode" not in SUPABASE_URL:
    SUPABASE_URL += ("&" if "?" in SUPABASE_URL else "?") + "sslmode=require"

print("Connecting to local SQLite...")
src = sqlite3.connect(LOCAL_DB)
src.row_factory = sqlite3.Row

print("Connecting to Neon PostgreSQL...")
dst = psycopg2.connect(SUPABASE_URL)
dst.autocommit = False
cur = dst.cursor()

TABLES = ["school", "user", "student", "subject", "mark", "timetable", "invoice", "payment"]

# ── SCHEMA (INTEGER for active fields to match SQLite) ───────────────────
SCHEMA = """
DROP TABLE IF EXISTS payment, invoice, timetable, mark, subject, student, "user", school CASCADE;

CREATE TABLE school (
    id SERIAL PRIMARY KEY,
    name VARCHAR(120) NOT NULL,
    reg_number VARCHAR(40) UNIQUE,
    address VARCHAR(200),
    phone VARCHAR(20),
    email VARCHAR(120),
    active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE "user" (
    id SERIAL PRIMARY KEY,
    username VARCHAR(80) UNIQUE NOT NULL,
    password_hash VARCHAR(200) NOT NULL,
    role VARCHAR(20) NOT NULL,
    full_name VARCHAR(120),
    email VARCHAR(120),
    school_id INTEGER REFERENCES school(id),
    active INTEGER DEFAULT 1
);

CREATE TABLE student (
    id SERIAL PRIMARY KEY,
    full_name VARCHAR(120) NOT NULL,
    adm_number VARCHAR(20) UNIQUE,
    class_name VARCHAR(20),
    stream VARCHAR(10),
    dob VARCHAR(20),
    gender VARCHAR(10),
    parent_id INTEGER REFERENCES "user"(id),
    school_id INTEGER REFERENCES school(id),
    active INTEGER DEFAULT 1
);

CREATE TABLE subject (
    id SERIAL PRIMARY KEY,
    name VARCHAR(80),
    code VARCHAR(10),
    school_id INTEGER REFERENCES school(id),
    teacher_id INTEGER REFERENCES "user"(id)
);

CREATE TABLE mark (
    id SERIAL PRIMARY KEY,
    student_id INTEGER REFERENCES student(id),
    subject_id INTEGER REFERENCES subject(id),
    teacher_id INTEGER REFERENCES "user"(id),
    score FLOAT,
    max_score FLOAT DEFAULT 100,
    exam_type VARCHAR(30),
    term VARCHAR(10),
    year INTEGER,
    school_id INTEGER REFERENCES school(id),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE timetable (
    id SERIAL PRIMARY KEY,
    school_id INTEGER REFERENCES school(id),
    class_name VARCHAR(20),
    stream VARCHAR(10),
    subject_id INTEGER REFERENCES subject(id),
    teacher_id INTEGER REFERENCES "user"(id),
    day VARCHAR(10),
    start_time VARCHAR(10),
    end_time VARCHAR(10)
);

CREATE TABLE invoice (
    id SERIAL PRIMARY KEY,
    invoice_number VARCHAR(20) UNIQUE,
    student_id INTEGER REFERENCES student(id),
    school_id INTEGER REFERENCES school(id),
    amount FLOAT,
    description VARCHAR(200),
    term VARCHAR(10),
    year INTEGER,
    status VARCHAR(20) DEFAULT 'unpaid',
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE payment (
    id SERIAL PRIMARY KEY,
    invoice_id INTEGER REFERENCES invoice(id),
    control_number VARCHAR(20) UNIQUE,
    amount_paid FLOAT,
    payment_method VARCHAR(30),
    receipt_number VARCHAR(20) UNIQUE,
    paid_at TIMESTAMP DEFAULT NOW(),
    school_id INTEGER REFERENCES school(id),
    created_by INTEGER REFERENCES "user"(id)
);
"""

print("Recreating tables...")
cur.execute(SCHEMA)
dst.commit()
print("Tables recreated.")

QUOTED = {"user": '"user"', "timetable": "timetable",
          "school": "school", "student": "student",
          "subject": "subject", "mark": "mark",
          "invoice": "invoice", "payment": "payment"}

try:
    total_ok = 0
    total_fail = 0
    for name in TABLES:
        rows = src.execute(f'SELECT * FROM {name}').fetchall()
        if not rows:
            print(f"  {name}: 0 rows")
            continue
        col_names = [desc[0] for desc in src.execute(f'SELECT * FROM {name} LIMIT 0').description]
        placeholders = ", ".join(["%s"] * len(col_names))
        cols = ", ".join(col_names)
        quoted = QUOTED[name]
        ok = 0
        for row in rows:
            vals = [row[c] for c in col_names]
            try:
                cur.execute(f"INSERT INTO {quoted} ({cols}) VALUES ({placeholders})", vals)
                dst.commit()
                ok += 1
            except Exception as ex:
                dst.rollback()
                print(f"  FAIL {name} id={row['id']}: {ex}")
        total_ok += ok
        total_fail += len(rows) - ok
        print(f"  {name}: {ok}/{len(rows)} rows migrated")

    print(f"\nDone! {total_ok} rows migrated, {total_fail} failed.")
except Exception as e:
    dst.rollback()
    print(f"\nERROR: {e}")
finally:
    src.close()
    dst.close()
