# 🎓 Elimu System — School Management Platform
A full-featured school management web application built with Flask (Python).

---

## 👥 User Roles & Demo Credentials

| Role         | Username      | Password     | Access                                      |
|--------------|---------------|--------------|---------------------------------------------|
| System Admin | `admin`       | `admin123`   | All schools, all users, platform oversight  |
| School Admin | `schooladmin` | `admin123`   | Students, subjects, staff management        |
| Accountant   | `accountant`  | `acc123`     | Invoices, control numbers, receipts (PDF)   |
| Teacher      | `teacher1`    | `teacher123` | Marks entry, timetable, result reports      |
| Parent       | `parent1`     | `parent123`  | View child results & fee status             |

---

## ⚙️ Setup & Run (Any Computer)

### Requirements
- Python 3.8+ installed
- pip installed

### Steps

```bash
# 1. Go into the project folder
cd elimu

# 2. Install dependencies
pip install flask flask-sqlalchemy flask-login werkzeug reportlab

# 3. Run the app
python app.py
```

### 4. Open in browser
```
http://localhost:5000
```

That's it! The database (`elimu.db`) is created and seeded automatically on first run.

---

## ✨ Features by Role

### System Admin
- Register and manage schools
- Activate / deactivate schools and users
- View all users across the platform

### School Admin
- Enrol students (auto-generates admission numbers)
- Add subjects and assign teachers
- Create staff/parent accounts

### Accountant
- Create fee invoices per student
- Generate control numbers for payment
- Record payments (M-Pesa, Bank Transfer, Cash, etc.)
- Download official PDF receipts

### Teacher
- Enter marks per student/subject/exam type
- View and manage weekly timetable (visual grid)
- Generate class results with rankings and grades
- Download results as PDF report

### Parent
- View all linked children
- See academic results per term/year
- View fee invoices and payment status

---

## 📁 Project Structure

```
elimu/
├── app.py                   # Main Flask app (models + routes)
├── elimu.db                 # SQLite database (auto-created)
├── requirements.txt         # Python dependencies
└── templates/
    ├── base.html            # Shared sidebar layout
    ├── login.html           # Login page
    ├── sysadmin/            # System admin pages
    ├── schooladmin/         # School admin pages
    ├── accountant/          # Finance pages
    ├── teacher/             # Teacher pages
    └── parent/              # Parent portal
```

---

## 📦 Transferring to Another Computer

1. Copy the entire `elimu/` folder (zip it)
2. On the new machine, install Python and run:
   ```
   pip install flask flask-sqlalchemy flask-login werkzeug reportlab
   python app.py
   ```
3. Delete `elimu.db` first if you want a fresh database with demo data re-seeded.

---

## 🛠 Tech Stack
- **Backend**: Python Flask + SQLAlchemy ORM
- **Database**: SQLite (zero config, file-based)
- **Auth**: Flask-Login with hashed passwords (Werkzeug)
- **PDF**: ReportLab (receipts + result reports)
- **Frontend**: Vanilla HTML/CSS (no frameworks, fully self-contained)

---

*Built for demonstration · Tanzania 🇹🇿*

# Elimu
