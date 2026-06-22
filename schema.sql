-- Elimu system tables for Supabase
-- Run this in Supabase SQL Editor (https://app.supabase.com → SQL Editor)

CREATE TABLE IF NOT EXISTS school (
    id SERIAL PRIMARY KEY,
    name VARCHAR(120) NOT NULL,
    reg_number VARCHAR(40) UNIQUE,
    address VARCHAR(200),
    phone VARCHAR(20),
    email VARCHAR(120),
    active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS "user" (
    id SERIAL PRIMARY KEY,
    username VARCHAR(80) UNIQUE NOT NULL,
    password_hash VARCHAR(200) NOT NULL,
    role VARCHAR(20) NOT NULL,
    full_name VARCHAR(120),
    email VARCHAR(120),
    school_id INTEGER REFERENCES school(id),
    active BOOLEAN DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS student (
    id SERIAL PRIMARY KEY,
    full_name VARCHAR(120) NOT NULL,
    adm_number VARCHAR(20) UNIQUE,
    class_name VARCHAR(20),
    stream VARCHAR(10),
    dob VARCHAR(20),
    gender VARCHAR(10),
    parent_id INTEGER REFERENCES "user"(id),
    school_id INTEGER REFERENCES school(id),
    active BOOLEAN DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS subject (
    id SERIAL PRIMARY KEY,
    name VARCHAR(80),
    code VARCHAR(10),
    school_id INTEGER REFERENCES school(id),
    teacher_id INTEGER REFERENCES "user"(id)
);

CREATE TABLE IF NOT EXISTS mark (
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

CREATE TABLE IF NOT EXISTS timetable (
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

CREATE TABLE IF NOT EXISTS invoice (
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

CREATE TABLE IF NOT EXISTS payment (
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
