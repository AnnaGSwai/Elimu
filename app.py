from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, make_response, send_file, session
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date, timedelta
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.units import cm
import io, random, string, os, json, secrets

# Load .env so Gmail credentials are available locally
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from services import notifications
from services import marks_excel

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'elimu-secret-2025')
app.config['MAX_CONTENT_LENGTH'] = 4 * 1024 * 1024  # 4 MB upload limit
_db_url = os.environ.get('DATABASE_URL', 'sqlite:///database.db')
if _db_url.startswith('postgres://'):
    _db_url = _db_url.replace('postgres://', 'postgresql://', 1)
if 'postgresql' in _db_url and 'sslmode' not in _db_url:
    _db_url += ('&' if '?' in _db_url else '?') + 'sslmode=require'
app.config['SQLALCHEMY_DATABASE_URI'] = _db_url
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {'pool_pre_ping': True, 'pool_recycle': 300}
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# Standard class levels used across enrolment and grouping
CLASS_LEVELS = [
    'Class 1', 'Class 2', 'Class 3', 'Class 4',
    'Class 5', 'Class 6', 'Class 7',
]

PASSWORD_RESET_OTP_MINUTES = 10
PASSWORD_RESET_OTP_LENGTH = 6

# Helper: ensure DB is ready
def _init_db():
    try:
        db.create_all()
        _ensure_user_phone_column()
        _ensure_pending_payment_receipts()
        School.query.filter(School.name.like('%Secondary%')).update({'name': db.func.replace(School.name, 'Secondary', 'Primary')}, synchronize_session=False)
        db.session.commit()
        if not User.query.filter_by(username='admin').first():
            seed()
        return True
    except Exception as ex:
        print(f"[ELIMU] DB init error: {ex}")
        return False

def _ensure_user_phone_column():
    """Add user.phone if missing (create_all does not alter existing tables)."""
    try:
        dialect = db.engine.dialect.name
        with db.engine.connect() as conn:
            if dialect == 'sqlite':
                rows = conn.execute(db.text("PRAGMA table_info(user)")).fetchall()
                cols = {r[1] for r in rows}
                if 'phone' not in cols:
                    conn.execute(db.text('ALTER TABLE user ADD COLUMN phone VARCHAR(20)'))
                    conn.commit()
            else:
                exists = conn.execute(db.text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name='user' AND column_name='phone'"
                )).fetchone()
                if not exists:
                    conn.execute(db.text('ALTER TABLE "user" ADD COLUMN phone VARCHAR(20)'))
                    conn.commit()
    except Exception as ex:
        print(f"[ELIMU] phone column check: {ex}")

def _ensure_pending_payment_receipts():
    """Pending payments must use NULL receipt_number (empty string breaks UNIQUE)."""
    try:
        db.session.execute(
            db.text("UPDATE payment SET receipt_number = NULL WHERE receipt_number = ''")
        )
        db.session.commit()
    except Exception as ex:
        db.session.rollback()
        print(f"[ELIMU] pending payment receipt fix: {ex}")

# ─── MODELS ───────────────────────────────────────────────────────────────────

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # sysadmin, schooladmin, accountant, teacher, parent
    full_name = db.Column(db.String(120))
    email = db.Column(db.String(120))
    phone = db.Column(db.String(20))
    school_id = db.Column(db.Integer, db.ForeignKey('school.id'), nullable=True)
    active = db.Column(db.Integer, default=1)

    def set_password(self, pw): self.password_hash = generate_password_hash(pw)
    def check_password(self, pw): return check_password_hash(self.password_hash, pw)

class School(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    reg_number = db.Column(db.String(40), unique=True)
    address = db.Column(db.String(200))
    phone = db.Column(db.String(20))
    email = db.Column(db.String(120))
    active = db.Column(db.Integer, default=1)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Student(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(120), nullable=False)
    adm_number = db.Column(db.String(20), unique=True)
    class_name = db.Column(db.String(20))
    stream = db.Column(db.String(10))
    dob = db.Column(db.String(20))
    gender = db.Column(db.String(10))
    parent_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    school_id = db.Column(db.Integer, db.ForeignKey('school.id'))
    active = db.Column(db.Integer, default=1)

class Subject(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80))
    code = db.Column(db.String(10))
    school_id = db.Column(db.Integer, db.ForeignKey('school.id'))
    teacher_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

class Mark(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'))
    subject_id = db.Column(db.Integer, db.ForeignKey('subject.id'))
    teacher_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    score = db.Column(db.Float)
    max_score = db.Column(db.Float, default=100)
    exam_type = db.Column(db.String(30))  # CAT1, CAT2, Midterm, Final
    term = db.Column(db.String(10))
    year = db.Column(db.Integer)
    school_id = db.Column(db.Integer, db.ForeignKey('school.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Timetable(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey('school.id'))
    class_name = db.Column(db.String(20))
    stream = db.Column(db.String(10))
    subject_id = db.Column(db.Integer, db.ForeignKey('subject.id'))
    teacher_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    day = db.Column(db.String(10))
    start_time = db.Column(db.String(10))
    end_time = db.Column(db.String(10))

class Invoice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    invoice_number = db.Column(db.String(20), unique=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'))
    school_id = db.Column(db.Integer, db.ForeignKey('school.id'))
    amount = db.Column(db.Float)
    description = db.Column(db.String(200))
    term = db.Column(db.String(10))
    year = db.Column(db.Integer)
    status = db.Column(db.String(20), default='unpaid')  # unpaid, paid, partial
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey('invoice.id'))
    control_number = db.Column(db.String(20), unique=True)
    amount_paid = db.Column(db.Float)
    payment_method = db.Column(db.String(30))
    receipt_number = db.Column(db.String(20), unique=True, nullable=True)
    paid_at = db.Column(db.DateTime, default=datetime.utcnow)
    school_id = db.Column(db.Integer, db.ForeignKey('school.id'))
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'))

class Announcement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    author_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    target_role = db.Column(db.String(20), nullable=False)
    school_id = db.Column(db.Integer, db.ForeignKey('school.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    author = db.relationship('User', backref='announcements', lazy=True)

class StudentEnrollmentRequest(db.Model):
    """Parent-submitted child registration awaiting school admin approval."""
    id = db.Column(db.Integer, primary_key=True)
    parent_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    school_id = db.Column(db.Integer, db.ForeignKey('school.id'), nullable=False)
    full_name = db.Column(db.String(120), nullable=False)
    class_name = db.Column(db.String(20), nullable=False)
    stream = db.Column(db.String(10), default='')
    dob = db.Column(db.String(20))
    gender = db.Column(db.String(10))
    parent_notes = db.Column(db.Text)
    status = db.Column(db.String(20), default='pending')  # pending | approved | rejected
    admin_feedback = db.Column(db.Text)
    reviewed_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    parent = db.relationship('User', foreign_keys=[parent_id], backref='enrollment_requests')
    reviewer = db.relationship('User', foreign_keys=[reviewed_by])
    student = db.relationship('Student', foreign_keys=[student_id])

class PasswordResetOtp(db.Model):
    """One-time code for parent password reset (email OTP)."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    otp_hash = db.Column(db.String(200), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    used = db.Column(db.Integer, default=0)
    attempts = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref='password_reset_otps')

@login_manager.user_loader
def load_user(uid): return User.query.get(int(uid))

def gen_code(prefix, n=8):
    return prefix + ''.join(random.choices(string.digits, k=n))

def grade(score):
    if score >= 75: return 'A'
    if score >= 65: return 'B'
    if score >= 50: return 'C'
    if score >= 40: return 'D'
    return 'F'

def _school_name(school_id):
    """Resolve school display name for emails."""
    if not school_id:
        return None
    s = School.query.get(school_id)
    return s.name if s else None

def _gen_password_reset_otp():
    """Cryptographically secure numeric OTP."""
    return ''.join(secrets.choice(string.digits) for _ in range(PASSWORD_RESET_OTP_LENGTH))

def _find_parent_for_password_reset(identifier):
    """Look up active parent by username or email."""
    identifier = (identifier or '').strip()
    if not identifier:
        return None
    user = User.query.filter_by(username=identifier, role='parent', active=1).first()
    if user:
        return user
    email = identifier.lower()
    return User.query.filter(
        db.func.lower(User.email) == email,
        User.role == 'parent',
        User.active == 1,
    ).first()

def _issue_password_reset_otp(user):
    """
    Invalidate old codes, store a new OTP hash, and email the plain OTP.
    Returns (success: bool, otp_or_error: str).
    """
    if not user.email:
        return False, 'no_email'
    if not notifications.channels_status().get('email'):
        return False, 'email_not_configured'

    otp = _gen_password_reset_otp()
    PasswordResetOtp.query.filter_by(user_id=user.id, used=0).update({'used': 1})
    record = PasswordResetOtp(
        user_id=user.id,
        otp_hash=generate_password_hash(otp),
        expires_at=datetime.utcnow() + timedelta(minutes=PASSWORD_RESET_OTP_MINUTES),
    )
    db.session.add(record)
    db.session.commit()

    sent = notifications.notify_password_reset_otp(
        user, otp, PASSWORD_RESET_OTP_MINUTES, _school_name(user.school_id)
    )
    if not sent.get('email'):
        db.session.delete(record)
        db.session.commit()
        return False, 'send_failed'
    return True, otp

def _verify_password_reset_otp(user_id, otp):
    """Validate OTP for the latest active reset request."""
    otp = (otp or '').strip()
    if not otp:
        return None, 'missing'

    record = PasswordResetOtp.query.filter_by(
        user_id=user_id, used=0
    ).order_by(PasswordResetOtp.created_at.desc()).first()
    if not record:
        return None, 'invalid'
    if record.expires_at < datetime.utcnow():
        record.used = 1
        db.session.commit()
        return None, 'expired'

    record.attempts = (record.attempts or 0) + 1
    if record.attempts > 5:
        record.used = 1
        db.session.commit()
        return None, 'locked'

    if not check_password_hash(record.otp_hash, otp):
        db.session.commit()
        return None, 'invalid'

    record.used = 1
    db.session.commit()
    return record, 'ok'

# ─── AUTH ─────────────────────────────────────────────────────────────────────

@app.route('/', methods=['GET','POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    _init_db()
    if request.method == 'POST':
        u = User.query.filter_by(username=request.form['username']).first()
        if u and u.check_password(request.form['password']) and u.active:
            login_user(u)
            return redirect(url_for('dashboard'))
        flash('Invalid credentials', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    role = current_user.role
    if role == 'sysadmin': return redirect(url_for('sysadmin_dashboard'))
    if role == 'schooladmin': return redirect(url_for('schooladmin_dashboard'))
    if role == 'accountant': return redirect(url_for('accountant_dashboard'))
    if role == 'teacher': return redirect(url_for('teacher_dashboard'))
    if role == 'parent': return redirect(url_for('parent_dashboard'))
    return redirect(url_for('login'))

# ─── PARENT REGISTRATION ──────────────────────────────────────────────────────

@app.route('/register', methods=['GET','POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    schools = School.query.filter_by(active=1).all()
    if request.method == 'POST':
        username = request.form['username'].strip()
        if User.query.filter_by(username=username).first():
            flash('Username already taken. Please choose another.', 'danger')
            return render_template('register.html', schools=schools)
        if request.form['password'] != request.form['confirm_password']:
            flash('Passwords do not match.', 'danger')
            return render_template('register.html', schools=schools)
        role = 'parent'  # Public self-registration is parent-only
        u = User(
            username=username,
            full_name=request.form['full_name'].strip(),
            email=request.form.get('email','').strip(),
            phone=request.form.get('phone','').strip() or None,
            role=role,
            school_id=request.form.get('school_id') or None,
            active=1
        )
        u.set_password(request.form['password'])
        db.session.add(u)
        db.session.commit()
        flash('Account created! You can now log in.', 'success')
        return redirect(url_for('login'))
    return render_template('register.html', schools=schools)

@app.route('/parent/forgot-password', methods=['GET', 'POST'])
def parent_forgot_password():
    """Parent-only: request a password reset OTP by username or email."""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    _init_db()

    if request.method == 'POST':
        identifier = request.form.get('identifier', '').strip()
        user = _find_parent_for_password_reset(identifier)

        if not user:
            flash(
                'If a parent account exists with those details, a reset code has been sent to its email.',
                'success'
            )
            return redirect(url_for('parent_forgot_password'))

        if not user.email:
            flash(
                'No email is linked to this account. Contact your school administrator to reset your password.',
                'danger'
            )
            return redirect(url_for('parent_forgot_password'))

        ok, detail = _issue_password_reset_otp(user)
        if detail == 'email_not_configured':
            flash('Password reset email is not configured. Please contact your school administrator.', 'danger')
            return redirect(url_for('parent_forgot_password'))
        if not ok:
            flash('Could not send the reset code. Please try again later or contact your school.', 'danger')
            return redirect(url_for('parent_forgot_password'))

        session['password_reset_user_id'] = user.id
        if user.email and '@' in user.email:
            local, domain = user.email.split('@', 1)
            hint = (local[:2] + '***@' + domain) if len(local) > 2 else ('***@' + domain)
        else:
            hint = 'your email'
        session['password_reset_email_hint'] = hint
        flash(f'A 6-digit code was sent to {user.email}. It expires in {PASSWORD_RESET_OTP_MINUTES} minutes.', 'success')
        return redirect(url_for('parent_reset_password'))

    return render_template('parent/forgot_password.html')

@app.route('/parent/reset-password', methods=['GET', 'POST'])
def parent_reset_password():
    """Parent-only: verify OTP and set a new password."""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    _init_db()

    user_id = session.get('password_reset_user_id')
    if not user_id:
        flash('Request a password reset code first.', 'danger')
        return redirect(url_for('parent_forgot_password'))

    user = User.query.filter_by(id=user_id, role='parent', active=1).first()
    if not user:
        session.pop('password_reset_user_id', None)
        session.pop('password_reset_email_hint', None)
        flash('Reset session expired. Please start again.', 'danger')
        return redirect(url_for('parent_forgot_password'))

    if request.method == 'POST':
        otp = request.form.get('otp', '').strip()
        new_pw = request.form.get('new_password', '').strip()
        confirm_pw = request.form.get('confirm_password', '').strip()

        if len(new_pw) < 6:
            flash('Password must be at least 6 characters.', 'danger')
            return redirect(url_for('parent_reset_password'))
        if new_pw != confirm_pw:
            flash('Passwords do not match.', 'danger')
            return redirect(url_for('parent_reset_password'))

        record, status = _verify_password_reset_otp(user.id, otp)
        if status == 'expired':
            flash('That code has expired. Request a new one.', 'danger')
            return redirect(url_for('parent_forgot_password'))
        if status == 'locked':
            flash('Too many failed attempts. Request a new code.', 'danger')
            return redirect(url_for('parent_forgot_password'))
        if status != 'ok':
            flash('Invalid verification code. Check the email and try again.', 'danger')
            return redirect(url_for('parent_reset_password'))

        user.set_password(new_pw)
        db.session.commit()
        session.pop('password_reset_user_id', None)
        session.pop('password_reset_email_hint', None)
        flash('Password updated. You can now sign in with your new password.', 'success')
        return redirect(url_for('login'))

    email_hint = session.get('password_reset_email_hint', 'your email')
    return render_template('parent/reset_password.html', email_hint=email_hint)

# ─── SYSADMIN ─────────────────────────────────────────────────────────────────

@app.route('/sysadmin')
@login_required
def sysadmin_dashboard():
    if current_user.role != 'sysadmin': return redirect(url_for('dashboard'))
    schools = School.query.all()
    school_admins = User.query.filter_by(role='schooladmin').order_by(User.id.desc()).all()
    users = User.query.all()
    students = Student.query.all()
    return render_template(
        'sysadmin/dashboard.html',
        schools=schools,
        users=users,
        school_admins=school_admins,
        students=students,
    )

@app.route('/sysadmin/schools', methods=['GET','POST'])
@login_required
def manage_schools():
    if current_user.role != 'sysadmin': return redirect(url_for('dashboard'))
    if request.method == 'POST':
        s = School(
            name=request.form['name'],
            reg_number=request.form['reg_number'],
            address=request.form['address'],
            phone=request.form['phone'],
            email=request.form['email']
        )
        db.session.add(s)
        db.session.commit()
        flash(f'School "{s.name}" added successfully', 'success')
        return redirect(url_for('manage_schools'))
    schools = School.query.all()
    return render_template('sysadmin/schools.html', schools=schools)

@app.route('/sysadmin/schools/<int:sid>')
@login_required
def view_school(sid):
    """System Admin: detailed overview of one school."""
    if current_user.role != 'sysadmin':
        return redirect(url_for('dashboard'))
    school = School.query.get_or_404(sid)
    school_admins = User.query.filter_by(school_id=sid, role='schooladmin').order_by(User.full_name).all()
    teachers_n = User.query.filter_by(school_id=sid, role='teacher').count()
    accountants_n = User.query.filter_by(school_id=sid, role='accountant').count()
    parents_n = User.query.filter_by(school_id=sid, role='parent').count()
    students_q = Student.query.filter_by(school_id=sid)
    students_n = students_q.count()
    students_active = students_q.filter_by(active=1).count()
    subjects_n = Subject.query.filter_by(school_id=sid).count()
    invoices = Invoice.query.filter_by(school_id=sid).all()
    paid = sum(1 for i in invoices if i.status == 'paid')
    unpaid = sum(1 for i in invoices if i.status == 'unpaid')
    total_billed = sum(i.amount or 0 for i in invoices)
    stats = {
        'admins': len(school_admins),
        'teachers': teachers_n,
        'accountants': accountants_n,
        'parents': parents_n,
        'students': students_n,
        'students_active': students_active,
        'subjects': subjects_n,
        'invoices': len(invoices),
        'invoices_paid': paid,
        'invoices_unpaid': unpaid,
        'total_billed': total_billed,
    }
    return render_template(
        'sysadmin/school_detail.html',
        school=school,
        stats=stats,
        school_admins=school_admins,
    )

@app.route('/sysadmin/schools/<int:sid>/toggle')
@login_required
def toggle_school(sid):
    if current_user.role != 'sysadmin': return redirect(url_for('dashboard'))
    s = School.query.get_or_404(sid)
    s.active = 0 if s.active else 1
    db.session.commit()
    flash(f'School {"activated" if s.active else "deactivated"}', 'success')
    return redirect(url_for('manage_schools'))

@app.route('/sysadmin/schools/<int:sid>/delete')
@login_required
def delete_school(sid):
    if current_user.role != 'sysadmin': return redirect(url_for('dashboard'))
    s = School.query.get_or_404(sid)
    if User.query.filter_by(school_id=sid).first() or Student.query.filter_by(school_id=sid).first():
        flash('Cannot delete school with active users or students. Deactivate it instead.', 'danger')
        return redirect(url_for('manage_schools'))
    db.session.delete(s)
    db.session.commit()
    flash(f'School "{s.name}" deleted permanently.', 'success')
    return redirect(url_for('manage_schools'))

@app.route('/sysadmin/users', methods=['GET','POST'])
@login_required
def manage_users():
    """System Admin manages school admins only (not teachers/parents/accountants)."""
    if current_user.role != 'sysadmin': return redirect(url_for('dashboard'))
    schools = School.query.filter_by(active=1).all()
    if request.method == 'POST':
        role = request.form.get('role', '')
        school_id = request.form.get('school_id') or None
        if role != 'schooladmin':
            flash('System Admin can only create School Admin accounts.', 'danger')
            return redirect(url_for('manage_users'))
        if not school_id:
            flash('Please assign the School Admin to a school.', 'danger')
            return redirect(url_for('manage_users'))
        if not School.query.get(int(school_id)):
            flash('Selected school not found.', 'danger')
            return redirect(url_for('manage_users'))
        u = User(
            username=request.form['username'],
            full_name=request.form['full_name'],
            email=request.form['email'],
            phone=request.form.get('phone', '').strip() or None,
            role='schooladmin',
            school_id=int(school_id)
        )
        u.set_password(request.form['password'])
        db.session.add(u)
        db.session.commit()
        flash('School Admin created successfully', 'success')
        return redirect(url_for('manage_users'))
    users = User.query.filter_by(role='schooladmin').order_by(User.full_name).all()
    return render_template('sysadmin/users.html', users=users, schools=schools)

@app.route('/sysadmin/users/<int:uid>/edit', methods=['POST'])
@login_required
def edit_user(uid):
    """System Admin updates a school admin account."""
    if current_user.role != 'sysadmin':
        return redirect(url_for('dashboard'))
    u = User.query.get_or_404(uid)
    if u.role != 'schooladmin':
        flash('System Admin can only manage School Admin accounts.', 'danger')
        return redirect(url_for('manage_users'))

    school_id = request.form.get('school_id') or None
    if not school_id:
        flash('Please assign the School Admin to a school.', 'danger')
        return redirect(url_for('manage_users'))
    if not School.query.get(int(school_id)):
        flash('Selected school not found.', 'danger')
        return redirect(url_for('manage_users'))

    new_username = request.form['username'].strip()
    if not new_username:
        flash('Username is required.', 'danger')
        return redirect(url_for('manage_users'))
    clash = User.query.filter(User.username == new_username, User.id != u.id).first()
    if clash:
        flash('Username already taken.', 'danger')
        return redirect(url_for('manage_users'))

    u.username = new_username
    u.full_name = request.form['full_name'].strip()
    u.email = request.form.get('email', '').strip() or None
    u.phone = request.form.get('phone', '').strip() or None
    u.school_id = int(school_id)
    new_pw = request.form.get('password', '').strip()
    if new_pw:
        u.set_password(new_pw)
    db.session.commit()
    flash(f'School Admin "{u.username}" updated.', 'success')
    return redirect(url_for('manage_users'))

@app.route('/sysadmin/users/<int:uid>/toggle')
@login_required
def toggle_user(uid):
    if current_user.role != 'sysadmin': return redirect(url_for('dashboard'))
    u = User.query.get_or_404(uid)
    if u.role != 'schooladmin':
        flash('System Admin can only manage School Admin accounts.', 'danger')
        return redirect(url_for('manage_users'))
    u.active = 0 if u.active else 1
    db.session.commit()
    flash('User status updated', 'success')
    return redirect(url_for('manage_users'))

@app.route('/sysadmin/users/<int:uid>/delete')
@login_required
def delete_user(uid):
    if current_user.role != 'sysadmin': return redirect(url_for('dashboard'))
    if uid == current_user.id:
        flash('You cannot delete your own account.', 'danger')
        return redirect(url_for('manage_users'))
    u = User.query.get_or_404(uid)
    if u.role != 'schooladmin':
        flash('System Admin can only manage School Admin accounts.', 'danger')
        return redirect(url_for('manage_users'))
    db.session.delete(u)
    db.session.commit()
    flash(f'School Admin "{u.username}" deleted permanently.', 'success')
    return redirect(url_for('manage_users'))

# ─── SCHOOL ADMIN ─────────────────────────────────────────────────────────────

@app.route('/schooladmin')
@login_required
def schooladmin_dashboard():
    if current_user.role != 'schooladmin': return redirect(url_for('dashboard'))
    sid = current_user.school_id
    school = School.query.get(sid)
    students = Student.query.filter_by(school_id=sid, active=1).all()
    teachers = User.query.filter_by(school_id=sid, role='teacher', active=1).all()
    subjects = Subject.query.filter_by(school_id=sid).all()
    return render_template('schooladmin/dashboard.html', school=school, students=students, teachers=teachers, subjects=subjects)

@app.route('/schooladmin/students', methods=['GET','POST'])
@login_required
def manage_students():
    if current_user.role != 'schooladmin': return redirect(url_for('dashboard'))
    sid = current_user.school_id
    if request.method == 'POST':
        adm = gen_code('ADM', 6)
        parents = User.query.filter_by(school_id=sid, role='parent', active=1).all()
        s = Student(
            full_name=request.form['full_name'],
            adm_number=adm,
            class_name=request.form['class_name'],
            stream=request.form.get('stream', 'A'),
            dob=request.form.get('dob', ''),
            gender=request.form.get('gender', ''),
            school_id=sid,
            parent_id=request.form.get('parent_id') or None
        )
        db.session.add(s)
        db.session.commit()
        flash(f'Student added. Admission No: {adm}', 'success')
        enrolled_class = request.form.get('class_name', CLASS_LEVELS[0])
        return redirect(url_for('manage_students', cls=enrolled_class))
    selected_class = request.args.get('cls', CLASS_LEVELS[0])
    if selected_class not in CLASS_LEVELS:
        selected_class = CLASS_LEVELS[0]
    students = Student.query.filter_by(school_id=sid).order_by(
        Student.class_name, Student.stream, Student.full_name
    ).all()
    parents = User.query.filter_by(school_id=sid, role='parent', active=1).all()
    parent_map = {p.id: p for p in parents}
    students_by_class = {cls: [] for cls in CLASS_LEVELS}
    for s in students:
        bucket = s.class_name if s.class_name in students_by_class else None
        if bucket:
            students_by_class[bucket].append(s)
    class_counts = {cls: len(students_by_class[cls]) for cls in CLASS_LEVELS}
    class_students = students_by_class[selected_class]
    return render_template(
        'schooladmin/students.html',
        students=students,
        students_by_class=students_by_class,
        class_levels=CLASS_LEVELS,
        class_counts=class_counts,
        selected_class=selected_class,
        class_students=class_students,
        parents=parents,
        parent_map=parent_map,
    )

@app.route('/schooladmin/students/<int:stud_id>/edit', methods=['POST'])
@login_required
def edit_student(stud_id):
    """School Admin: update student details within their school."""
    if current_user.role != 'schooladmin': return redirect(url_for('dashboard'))
    sid = current_user.school_id
    stud = Student.query.get_or_404(stud_id)
    if stud.school_id != sid:
        flash('You can only edit students in your school.', 'danger')
        return redirect(url_for('manage_students', cls=request.form.get('return_class', CLASS_LEVELS[0])))

    full_name = request.form.get('full_name', '').strip()
    if not full_name:
        flash('Full name is required.', 'danger')
        return redirect(url_for('manage_students', cls=request.form.get('return_class', CLASS_LEVELS[0])))

    parent_id = request.form.get('parent_id') or None
    if parent_id:
        parent = User.query.filter_by(id=parent_id, school_id=sid, role='parent', active=1).first()
        if not parent:
            flash('Invalid parent selection.', 'danger')
            return redirect(url_for('manage_students', cls=request.form.get('return_class', CLASS_LEVELS[0])))
        parent_id = int(parent_id)

    stud.full_name = full_name
    stud.class_name = request.form.get('class_name', stud.class_name)
    stud.stream = request.form.get('stream', stud.stream)
    stud.dob = request.form.get('dob', '') or ''
    stud.gender = request.form.get('gender', '') or ''
    stud.parent_id = parent_id
    stud.active = 1 if request.form.get('active') == '1' else 0
    db.session.commit()
    flash(f'Student "{stud.full_name}" updated.', 'success')
    return redirect(url_for('manage_students', cls=stud.class_name))

@app.route('/schooladmin/students/<int:stud_id>/delete')
@login_required
def delete_student(stud_id):
    """Remove a student, or deactivate if they have marks/invoices."""
    if current_user.role != 'schooladmin': return redirect(url_for('dashboard'))
    sid = current_user.school_id
    stud = Student.query.get_or_404(stud_id)
    if stud.school_id != sid:
        flash('You can only delete students in your school.', 'danger')
        return redirect(url_for('manage_students'))

    name = stud.full_name
    student_class = stud.class_name or CLASS_LEVELS[0]
    has_marks = Mark.query.filter_by(student_id=stud.id).first()
    has_invoices = Invoice.query.filter_by(student_id=stud.id).first()
    if has_marks or has_invoices:
        # Soft-delete: keep academic/finance history intact
        stud.active = 0
        db.session.commit()
        flash(
            f'Student "{name}" has marks or invoices, so they were deactivated instead of deleted.',
            'warning'
        )
        return redirect(url_for('manage_students', cls=student_class))

    db.session.delete(stud)
    db.session.commit()
    flash(f'Student "{name}" deleted.', 'success')
    return redirect(url_for('manage_students', cls=student_class))

@app.route('/schooladmin/enrollment-requests')
@login_required
def manage_enrollment_requests():
    """School Admin: review parent-submitted child registration applications."""
    if current_user.role != 'schooladmin':
        return redirect(url_for('dashboard'))
    sid = current_user.school_id
    requests = StudentEnrollmentRequest.query.filter_by(
        school_id=sid
    ).order_by(StudentEnrollmentRequest.created_at.desc()).all()
    parents = {u.id: u for u in User.query.filter_by(school_id=sid, role='parent').all()}
    pending_count = sum(1 for r in requests if r.status == 'pending')
    return render_template(
        'schooladmin/enrollment_requests.html',
        requests=requests,
        parents=parents,
        pending_count=pending_count,
        class_levels=CLASS_LEVELS,
    )

@app.route('/schooladmin/enrollment-requests/<int:req_id>/approve', methods=['POST'])
@login_required
def approve_enrollment_request(req_id):
    if current_user.role != 'schooladmin':
        return redirect(url_for('dashboard'))
    sid = current_user.school_id
    req = StudentEnrollmentRequest.query.get_or_404(req_id)
    if req.school_id != sid:
        flash('You can only review applications for your school.', 'danger')
        return redirect(url_for('manage_enrollment_requests'))
    if req.status != 'pending':
        flash('This application has already been reviewed.', 'warning')
        return redirect(url_for('manage_enrollment_requests'))

    parent = User.query.filter_by(id=req.parent_id, school_id=sid, role='parent', active=1).first()
    if not parent:
        flash('Parent account not found or inactive.', 'danger')
        return redirect(url_for('manage_enrollment_requests'))

    adm = gen_code('ADM', 6)
    student = Student(
        full_name=req.full_name,
        adm_number=adm,
        class_name=req.class_name,
        stream=(req.stream or '').strip() or None,
        dob=req.dob or '',
        gender=req.gender or '',
        parent_id=req.parent_id,
        school_id=sid,
        active=1,
    )
    db.session.add(student)
    db.session.flush()

    feedback = request.form.get('admin_feedback', '').strip()
    req.status = 'approved'
    req.student_id = student.id
    req.reviewed_by = current_user.id
    req.reviewed_at = datetime.utcnow()
    if feedback:
        req.admin_feedback = feedback
    db.session.commit()
    flash(f'{req.full_name} admitted. Admission No: {adm}', 'success')
    return redirect(url_for('manage_enrollment_requests'))

@app.route('/schooladmin/enrollment-requests/<int:req_id>/reject', methods=['POST'])
@login_required
def reject_enrollment_request(req_id):
    if current_user.role != 'schooladmin':
        return redirect(url_for('dashboard'))
    sid = current_user.school_id
    req = StudentEnrollmentRequest.query.get_or_404(req_id)
    if req.school_id != sid:
        flash('You can only review applications for your school.', 'danger')
        return redirect(url_for('manage_enrollment_requests'))
    if req.status != 'pending':
        flash('This application has already been reviewed.', 'warning')
        return redirect(url_for('manage_enrollment_requests'))

    feedback = request.form.get('admin_feedback', '').strip()
    if not feedback:
        flash('Please provide feedback so the parent knows how to proceed.', 'danger')
        return redirect(url_for('manage_enrollment_requests'))

    req.status = 'rejected'
    req.admin_feedback = feedback
    req.reviewed_by = current_user.id
    req.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash(f'Application for {req.full_name} rejected. Feedback sent to parent.', 'info')
    return redirect(url_for('manage_enrollment_requests'))

@app.route('/schooladmin/subjects', methods=['GET','POST'])
@login_required
def manage_subjects():
    """School Admin: create subjects and assign teachers (live, not seed-only)."""
    if current_user.role != 'schooladmin': return redirect(url_for('dashboard'))
    sid = current_user.school_id
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        code = request.form.get('code', '').strip().upper()
        tid = request.form.get('teacher_id', '').strip()
        teacher_id = int(tid) if tid else None
        if not name or not code:
            flash('Subject name and code are required.', 'danger')
            return redirect(url_for('manage_subjects'))
        if teacher_id:
            t = User.query.filter_by(id=teacher_id, school_id=sid, role='teacher', active=1).first()
            if not t:
                flash('Invalid teacher selection.', 'danger')
                return redirect(url_for('manage_subjects'))
        clash = Subject.query.filter_by(school_id=sid, code=code).first()
        if clash:
            flash(f'Code "{code}" already exists for this school.', 'danger')
            return redirect(url_for('manage_subjects'))
        subj = Subject(name=name, code=code, school_id=sid, teacher_id=teacher_id)
        db.session.add(subj)
        db.session.commit()
        flash(f'Subject "{name}" added.', 'success')
        return redirect(url_for('manage_subjects'))
    subjects = Subject.query.filter_by(school_id=sid).order_by(Subject.name).all()
    teachers = User.query.filter_by(school_id=sid, role='teacher', active=1).order_by(User.full_name).all()
    return render_template('schooladmin/subjects.html', subjects=subjects, teachers=teachers)

@app.route('/schooladmin/subjects/<int:subj_id>/edit', methods=['POST'])
@login_required
def edit_subject(subj_id):
    """Update subject name/code and (re)assign teacher."""
    if current_user.role != 'schooladmin': return redirect(url_for('dashboard'))
    sid = current_user.school_id
    subj = Subject.query.get_or_404(subj_id)
    if subj.school_id != sid:
        flash('You can only edit subjects in your school.', 'danger')
        return redirect(url_for('manage_subjects'))

    name = request.form.get('name', '').strip()
    code = request.form.get('code', '').strip().upper()
    tid = request.form.get('teacher_id', '').strip()
    teacher_id = int(tid) if tid else None

    if not name or not code:
        flash('Subject name and code are required.', 'danger')
        return redirect(url_for('manage_subjects'))

    clash = Subject.query.filter(
        Subject.school_id == sid, Subject.code == code, Subject.id != subj.id
    ).first()
    if clash:
        flash(f'Code "{code}" already exists for this school.', 'danger')
        return redirect(url_for('manage_subjects'))

    if teacher_id:
        t = User.query.filter_by(id=teacher_id, school_id=sid, role='teacher', active=1).first()
        if not t:
            flash('Invalid teacher selection.', 'danger')
            return redirect(url_for('manage_subjects'))

    subj.name = name
    subj.code = code
    subj.teacher_id = teacher_id
    db.session.commit()
    flash(f'Subject "{subj.name}" updated.', 'success')
    return redirect(url_for('manage_subjects'))

@app.route('/schooladmin/subjects/<int:subj_id>/delete')
@login_required
def delete_subject(subj_id):
    """Remove a subject if it has no marks (timetable slots are cleared)."""
    if current_user.role != 'schooladmin': return redirect(url_for('dashboard'))
    sid = current_user.school_id
    subj = Subject.query.get_or_404(subj_id)
    if subj.school_id != sid:
        flash('You can only delete subjects in your school.', 'danger')
        return redirect(url_for('manage_subjects'))

    if Mark.query.filter_by(subject_id=subj.id).first():
        flash('Cannot delete: this subject has marks. Reassign the teacher instead.', 'danger')
        return redirect(url_for('manage_subjects'))

    Timetable.query.filter_by(subject_id=subj.id).delete()
    name = subj.name
    db.session.delete(subj)
    db.session.commit()
    flash(f'Subject "{name}" deleted.', 'success')
    return redirect(url_for('manage_subjects'))

def _assigned_subjects(sid, teacher_id):
    """Subjects assigned to a teacher for this school."""
    return Subject.query.filter_by(
        school_id=sid, teacher_id=teacher_id
    ).order_by(Subject.name).all()

@app.route('/schooladmin/staff', methods=['GET','POST'])
@login_required
def manage_staff():
    """School Admin manages school users: teachers, accountants, parents."""
    if current_user.role != 'schooladmin': return redirect(url_for('dashboard'))
    sid = current_user.school_id
    allowed_roles = ('teacher', 'accountant', 'parent')
    if request.method == 'POST':
        role = request.form.get('role', '')
        if role not in allowed_roles:
            flash('School Admin can only create teachers, accountants, or parents.', 'danger')
            return redirect(url_for('manage_staff'))
        u = User(
            username=request.form['username'],
            full_name=request.form['full_name'],
            email=request.form['email'],
            phone=request.form.get('phone', '').strip() or None,
            role=role,
            school_id=sid
        )
        u.set_password(request.form['password'])
        db.session.add(u)
        db.session.commit()
        flash(f'{role.capitalize()} account created', 'success')
        return redirect(url_for('manage_staff'))
    staff = User.query.filter(User.school_id==sid, User.role.in_(allowed_roles)).all()
    return render_template('schooladmin/staff.html', staff=staff)

@app.route('/schooladmin/staff/<int:uid>/edit', methods=['POST'])
@login_required
def edit_staff(uid):
    """School Admin updates a teacher, accountant, or parent in their school."""
    if current_user.role != 'schooladmin':
        return redirect(url_for('dashboard'))
    sid = current_user.school_id
    allowed_roles = ('teacher', 'accountant', 'parent')
    u = User.query.get_or_404(uid)
    if u.school_id != sid or u.role not in allowed_roles:
        flash('You can only edit school users in your school.', 'danger')
        return redirect(url_for('manage_staff'))

    role = request.form.get('role', '')
    if role not in allowed_roles:
        flash('Role must be teacher, accountant, or parent.', 'danger')
        return redirect(url_for('manage_staff'))

    new_username = request.form['username'].strip()
    if not new_username:
        flash('Username is required.', 'danger')
        return redirect(url_for('manage_staff'))
    clash = User.query.filter(User.username == new_username, User.id != u.id).first()
    if clash:
        flash('Username already taken.', 'danger')
        return redirect(url_for('manage_staff'))

    u.username = new_username
    u.full_name = request.form['full_name'].strip()
    u.email = request.form.get('email', '').strip() or None
    u.phone = request.form.get('phone', '').strip() or None
    u.role = role
    new_pw = request.form.get('password', '').strip()
    if new_pw:
        u.set_password(new_pw)
    db.session.commit()
    flash(f'{role.capitalize()} "{u.username}" updated.', 'success')
    return redirect(url_for('manage_staff'))

# ─── ACCOUNTANT ───────────────────────────────────────────────────────────────

@app.route('/accountant')
@login_required
def accountant_dashboard():
    if current_user.role != 'accountant': return redirect(url_for('dashboard'))
    sid = current_user.school_id
    invoices = Invoice.query.filter_by(school_id=sid).all()
    payments = Payment.query.filter_by(school_id=sid).all()
    total_billed = sum(i.amount for i in invoices)
    total_paid = sum(p.amount_paid for p in payments)
    return render_template('accountant/dashboard.html', invoices=invoices, payments=payments,
                           total_billed=total_billed, total_paid=total_paid)

@app.route('/accountant/invoices', methods=['GET','POST'])
@login_required
def manage_invoices():
    if current_user.role != 'accountant': return redirect(url_for('dashboard'))
    sid = current_user.school_id
    if request.method == 'POST':
        inv_no = gen_code('INV', 6)
        inv = Invoice(
            invoice_number=inv_no,
            student_id=request.form['student_id'],
            school_id=sid,
            amount=float(request.form['amount']),
            description=request.form['description'],
            term=request.form['term'],
            year=int(request.form['year'])
        )
        db.session.add(inv)
        db.session.commit()
        flash(f'Invoice {inv_no} created', 'success')
        return redirect(url_for('manage_invoices'))
    invoices = Invoice.query.filter_by(school_id=sid).order_by(Invoice.created_at.desc()).all()
    students = Student.query.filter_by(school_id=sid, active=1).all()
    student_map = {s.id: s for s in students}
    return render_template('accountant/invoices.html', invoices=invoices, students=students, student_map=student_map)

def _invoice_balance(inv):
    """Outstanding amount on an invoice after completed payments."""
    payments = Payment.query.filter_by(invoice_id=inv.id).all()
    paid = sum(p.amount_paid or 0 for p in payments if p.receipt_number)
    return max(0, (inv.amount or 0) - paid)

def _pending_payment_for_invoice(inv_id):
    """Return pending payment (control number issued, not yet paid) if any."""
    for p in Payment.query.filter_by(invoice_id=inv_id).all():
        if not p.receipt_number:
            return p
    return None

def _issue_control_number(inv, created_by_id):
    """
    Create a pending payment control number for an invoice with outstanding balance.
    Returns (payment, error_message). payment is set when created or already pending.
    """
    balance = _invoice_balance(inv)
    if balance <= 0:
        return None, 'This invoice is already fully paid.'

    existing = _pending_payment_for_invoice(inv.id)
    if existing:
        return existing, None

    cn = gen_code('CTR', 10)
    pay = Payment(
        invoice_id=inv.id,
        control_number=cn,
        amount_paid=0,
        payment_method='pending',
        receipt_number=None,
        school_id=inv.school_id,
        created_by=created_by_id,
    )
    db.session.add(pay)
    db.session.commit()
    return pay, None

@app.route('/accountant/control-number/<int:inv_id>')
@login_required
def generate_control_number(inv_id):
    if current_user.role != 'accountant': return redirect(url_for('dashboard'))
    inv = Invoice.query.get_or_404(inv_id)
    if inv.school_id != current_user.school_id:
        flash('You can only manage invoices for your school.', 'danger')
        return redirect(url_for('manage_invoices'))

    existing_before = _pending_payment_for_invoice(inv.id)
    pay, err = _issue_control_number(inv, current_user.id)
    if err:
        flash(err, 'warning')
        return redirect(url_for('manage_invoices'))
    if existing_before:
        flash(f'Control number already issued: {pay.control_number}', 'info')
    else:
        flash(f'Control Number generated: {pay.control_number}', 'success')
    return redirect(url_for('manage_invoices'))

@app.route('/accountant/record-payment/<int:pay_id>', methods=['POST'])
@login_required
def record_payment(pay_id):
    if current_user.role != 'accountant': return redirect(url_for('dashboard'))
    pay = Payment.query.get_or_404(pay_id)
    pay.amount_paid = float(request.form['amount_paid'])
    pay.payment_method = request.form['payment_method']
    pay.receipt_number = gen_code('RCP', 6)
    pay.paid_at = datetime.utcnow()
    inv = Invoice.query.get(pay.invoice_id)
    inv.status = 'paid' if pay.amount_paid >= inv.amount else 'partial'
    db.session.commit()
    flash(f'Payment recorded. Receipt: {pay.receipt_number}', 'success')
    return redirect(url_for('manage_payments'))

@app.route('/accountant/payments')
@login_required
def manage_payments():
    if current_user.role != 'accountant': return redirect(url_for('dashboard'))
    sid = current_user.school_id
    payments = Payment.query.filter_by(school_id=sid).order_by(Payment.paid_at.desc()).all()
    invoices = {i.id: i for i in Invoice.query.filter_by(school_id=sid).all()}
    students = {s.id: s for s in Student.query.filter_by(school_id=sid).all()}
    pending = [p for p in payments if not p.receipt_number]
    return render_template('accountant/payments.html', payments=payments, invoices=invoices,
                           students=students, pending=pending)

@app.route('/accountant/receipt/<int:pay_id>/pdf')
@login_required
def download_receipt(pay_id):
    pay = Payment.query.get_or_404(pay_id)
    inv = Invoice.query.get(pay.invoice_id)
    student = Student.query.get(inv.student_id) if inv else None
    school = School.query.get(pay.school_id)

    # Parents may only download receipts for their own children
    if current_user.role == 'parent':
        if not student or student.parent_id != current_user.id:
            flash('You can only view receipts for your children.', 'danger')
            return redirect(url_for('parent_payments'))
    elif current_user.role == 'accountant':
        if pay.school_id != current_user.school_id:
            flash('Access denied.', 'danger')
            return redirect(url_for('manage_payments'))
    elif current_user.role not in ('sysadmin', 'schooladmin'):
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard'))

    if not inv or not student or not school:
        flash('Receipt data is incomplete.', 'danger')
        return redirect(url_for('dashboard'))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=2*cm, bottomMargin=2*cm)
    styles = getSampleStyleSheet()
    story = []

    title_style = ParagraphStyle('title', fontSize=18, fontName='Helvetica-Bold', alignment=1, spaceAfter=6)
    sub_style = ParagraphStyle('sub', fontSize=11, alignment=1, spaceAfter=2)
    normal = styles['Normal']

    story.append(Paragraph(school.name.upper(), title_style))
    story.append(Paragraph(school.address or '', sub_style))
    story.append(Paragraph(f'Tel: {school.phone or "N/A"}  |  Email: {school.email or "N/A"}', sub_style))
    story.append(Spacer(1, 0.5*cm))
    story.append(Paragraph('OFFICIAL PAYMENT RECEIPT', ParagraphStyle('rt', fontSize=14, fontName='Helvetica-Bold', alignment=1, textColor=colors.HexColor('#1a6e3c'), spaceAfter=10)))

    data = [
        ['Receipt No:', pay.receipt_number, 'Date:', pay.paid_at.strftime('%d %b %Y') if pay.paid_at else 'N/A'],
        ['Student:', student.full_name, 'Adm No:', student.adm_number],
        ['Class:', student.class_name, 'Term:', f"{inv.term} {inv.year}"],
        ['Invoice No:', inv.invoice_number, 'Description:', inv.description],
        ['Amount Invoiced:', f'TZS {inv.amount:,.0f}', 'Amount Paid:', f'TZS {pay.amount_paid:,.0f}'],
        ['Payment Method:', pay.payment_method, 'Balance:', f'TZS {max(0, inv.amount - pay.amount_paid):,.0f}'],
        ['Control No:', pay.control_number, 'Status:', inv.status.upper()],
    ]
    t = Table(data, colWidths=[4*cm, 5.5*cm, 3.5*cm, 5.5*cm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (0,-1), colors.HexColor('#f0f7f4')),
        ('BACKGROUND', (2,0), (2,-1), colors.HexColor('#f0f7f4')),
        ('FONTNAME', (0,0), (0,-1), 'Helvetica-Bold'),
        ('FONTNAME', (2,0), (2,-1), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 10),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#cccccc')),
        ('ROWBACKGROUNDS', (0,0), (-1,-1), [colors.white, colors.HexColor('#fafafa')]),
        ('TEXTCOLOR', (3,4), (3,4), colors.HexColor('#1a6e3c')),
        ('FONTNAME', (3,4), (3,4), 'Helvetica-Bold'),
    ]))
    story.append(t)
    story.append(Spacer(1, 1*cm))
    story.append(Paragraph('This is a computer-generated receipt and is valid without a signature.', ParagraphStyle('foot', fontSize=9, alignment=1, textColor=colors.gray)))

    doc.build(story)
    buf.seek(0)
    response = make_response(buf.read())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename=receipt_{pay.receipt_number}.pdf'
    return response

# ─── TEACHER ──────────────────────────────────────────────────────────────────

@app.route('/teacher')
@login_required
def teacher_dashboard():
    if current_user.role != 'teacher': return redirect(url_for('dashboard'))
    sid = current_user.school_id
    subjects = _assigned_subjects(sid, current_user.id)
    marks = Mark.query.filter_by(teacher_id=current_user.id).all()
    timetable = Timetable.query.filter_by(school_id=sid, teacher_id=current_user.id).all()
    return render_template('teacher/dashboard.html', subjects=subjects, marks=marks, timetable=timetable)

@app.route('/teacher/students', methods=['GET', 'POST'])
@login_required
def teacher_manage_students():
    if current_user.role != 'teacher': return redirect(url_for('dashboard'))
    sid = current_user.school_id
    if request.method == 'POST':
        full_name = request.form['full_name']
        adm_number = request.form['adm_number']
        gender = request.form['gender']
        class_name = request.form['class_name']
        stream = request.form.get('stream', '')
        if Student.query.filter_by(school_id=sid, adm_number=adm_number).first():
            flash('Admission number already exists in the school!', 'error')
        else:
            s = Student(full_name=full_name, adm_number=adm_number, gender=gender,
                        class_name=class_name, stream=stream, school_id=sid)
            db.session.add(s)
            db.session.commit()
            flash('Student added successfully!', 'success')
        return redirect(url_for('teacher_manage_students'))
    
    students = Student.query.filter_by(school_id=sid).order_by(Student.class_name, Student.full_name).all()
    classes = [f'Class {i}' for i in range(1, 8)]
    return render_template('teacher/students.html', students=students, classes=classes)

@app.route('/teacher/marks', methods=['GET','POST'])
@login_required
def manage_marks():
    if current_user.role != 'teacher': return redirect(url_for('dashboard'))
    sid = current_user.school_id

    # Filter params for marklist view
    sel_subject = request.args.get('subject_id', '')
    sel_class   = request.args.get('class_name', '')
    sel_exam    = request.args.get('exam_type', 'CAT 1')
    sel_term    = request.args.get('term', 'Term 1')
    sel_year    = int(request.args.get('year', datetime.utcnow().year))

    if request.method == 'POST':
        form_type = request.form.get('form_type', 'single')
        if form_type == 'bulk':
            sub_id    = int(request.form['subject_id'])
            if not Subject.query.filter_by(id=sub_id, school_id=sid, teacher_id=current_user.id).first():
                flash('You can only enter marks for subjects assigned to you.', 'danger')
                return redirect(url_for('manage_marks'))
            exam_type = request.form['exam_type']
            term      = request.form['term']
            year      = int(request.form['year'])
            cls       = request.form['class_name']
            stud_list = Student.query.filter_by(school_id=sid, class_name=cls, active=1).all()
            saved = 0
            for stud in stud_list:
                val = request.form.get(f'score_{stud.id}', '').strip()
                if not val: continue
                try: score_f = float(val)
                except: continue
                if score_f < 0 or score_f > 100: continue
                ex = Mark.query.filter_by(
                    student_id=stud.id, subject_id=sub_id,
                    exam_type=exam_type, term=term, year=year
                ).first()
                if ex:
                    ex.score = score_f
                else:
                    db.session.add(Mark(
                        student_id=stud.id, subject_id=sub_id,
                        teacher_id=current_user.id, score=score_f,
                        exam_type=exam_type, term=term, year=year, school_id=sid
                    ))
                saved += 1
            db.session.commit()
            flash(f'{saved} marks saved for {exam_type}!', 'success')
            return redirect(url_for('manage_marks', subject_id=sub_id,
                class_name=cls, exam_type=exam_type, term=term, year=year))
        else:
            sub_id = int(request.form['subject_id'])
            if not Subject.query.filter_by(id=sub_id, school_id=sid, teacher_id=current_user.id).first():
                flash('You can only enter marks for subjects assigned to you.', 'danger')
                return redirect(url_for('manage_marks'))
            existing = Mark.query.filter_by(
                student_id=request.form['student_id'],
                subject_id=sub_id,
                exam_type=request.form['exam_type'],
                term=request.form['term'],
                year=int(request.form['year'])
            ).first()
            if existing:
                existing.score = float(request.form['score'])
                flash('Mark updated', 'success')
            else:
                db.session.add(Mark(
                    student_id=request.form['student_id'],
                    subject_id=sub_id,
                    teacher_id=current_user.id,
                    score=float(request.form['score']),
                    exam_type=request.form['exam_type'],
                    term=request.form['term'],
                    year=int(request.form['year']),
                    school_id=sid
                ))
                flash('Mark saved', 'success')
            db.session.commit()
            return redirect(url_for('manage_marks'))

    subjects  = _assigned_subjects(sid, current_user.id)
    all_studs = Student.query.filter_by(school_id=sid, active=1).all()
    classes   = [f'Class {i}' for i in range(1, 8)]

    marklist_students = []
    existing_marks    = {}
    if sel_subject and sel_class:
        # Only load marklist if subject is assigned to this teacher
        if not Subject.query.filter_by(
            id=int(sel_subject), school_id=sid, teacher_id=current_user.id
        ).first():
            flash('That subject is not assigned to you.', 'danger')
            return redirect(url_for('manage_marks'))
        marklist_students = Student.query.filter_by(
            school_id=sid, class_name=sel_class, active=1).all()
        ex_marks = Mark.query.filter_by(
            subject_id=int(sel_subject), exam_type=sel_exam,
            term=sel_term, year=sel_year, school_id=sid
        ).all()
        existing_marks = {m.student_id: m.score for m in ex_marks}

    marks    = Mark.query.filter_by(teacher_id=current_user.id, school_id=sid
               ).order_by(Mark.created_at.desc()).limit(50).all()
    subj_map = {s.id: s for s in Subject.query.filter_by(school_id=sid).all()}
    stud_map = {s.id: s for s in all_studs}

    return render_template('teacher/marks.html',
        subjects=subjects, students=all_studs, classes=classes,
        marks=marks, subj_map=subj_map, stud_map=stud_map, grade=grade,
        marklist_students=marklist_students, existing_marks=existing_marks,
        sel_subject=int(sel_subject) if sel_subject else None,
        sel_class=sel_class, sel_exam=sel_exam, sel_term=sel_term, sel_year=sel_year)

@app.route('/teacher/marks/excel-template')
@login_required
def download_marks_excel_template():
    """Download Excel template for the selected subject/class/exam."""
    if current_user.role != 'teacher':
        return redirect(url_for('dashboard'))
    sid = current_user.school_id
    try:
        subject_id = int(request.args['subject_id'])
    except (KeyError, TypeError, ValueError):
        flash('Select a subject and class before downloading the Excel template.', 'danger')
        return redirect(url_for('manage_marks'))

    class_name = request.args.get('class_name', '').strip()
    exam_type = request.args.get('exam_type', 'CAT 1').strip()
    term = request.args.get('term', 'Term 1').strip()
    try:
        year = int(request.args.get('year', datetime.utcnow().year))
    except (TypeError, ValueError):
        year = datetime.utcnow().year

    if not class_name:
        flash('Select a class before downloading the Excel template.', 'danger')
        return redirect(url_for('manage_marks'))

    subj = Subject.query.filter_by(
        id=subject_id, school_id=sid, teacher_id=current_user.id
    ).first()
    if not subj:
        flash('You can only download templates for subjects assigned to you.', 'danger')
        return redirect(url_for('manage_marks'))

    students = Student.query.filter_by(
        school_id=sid, class_name=class_name, active=1
    ).order_by(Student.full_name).all()
    if not students:
        flash(f'No students found in {class_name}.', 'danger')
        return redirect(url_for('manage_marks', subject_id=subject_id,
                                class_name=class_name, exam_type=exam_type,
                                term=term, year=year))

    existing = {
        m.student_id: m.score
        for m in Mark.query.filter_by(
            subject_id=subject_id, exam_type=exam_type,
            term=term, year=year, school_id=sid
        ).all()
    }
    data = marks_excel.build_marks_template(
        subject_id=subj.id,
        subject_name=subj.name,
        subject_code=subj.code or '',
        class_name=class_name,
        exam_type=exam_type,
        term=term,
        year=year,
        students=students,
        existing_marks=existing,
    )
    fname = marks_excel.safe_filename(
        'marks', subj.code or subj.name, class_name, exam_type, term, year
    ) + '.xlsx'
    return send_file(
        io.BytesIO(data),
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=fname,
    )

@app.route('/teacher/marks/excel-upload', methods=['POST'])
@login_required
def upload_marks_excel():
    """Import marks from an uploaded Elimu Excel template."""
    if current_user.role != 'teacher':
        return redirect(url_for('dashboard'))
    sid = current_user.school_id
    file = request.files.get('excel_file')
    if not file or not file.filename:
        flash('Please choose an Excel (.xlsx) file to upload.', 'danger')
        return redirect(url_for('manage_marks'))

    name = file.filename.lower()
    if not (name.endswith('.xlsx') or name.endswith('.xlsm')):
        flash('Only .xlsx Excel files are supported.', 'danger')
        return redirect(url_for('manage_marks'))

    try:
        meta, rows = marks_excel.parse_marks_workbook(file)
    except ValueError as ex:
        flash(str(ex), 'danger')
        return redirect(url_for('manage_marks'))

    subj = Subject.query.filter_by(
        id=meta['subject_id'], school_id=sid, teacher_id=current_user.id
    ).first()
    if not subj:
        flash('That subject is not assigned to you (or Info sheet is wrong).', 'danger')
        return redirect(url_for('manage_marks'))

    allowed_exams = {'CAT 1', 'CAT 2', 'Midterm', 'Final'}
    if meta['exam_type'] not in allowed_exams:
        flash(f'Invalid exam type on Info sheet: {meta["exam_type"]}', 'danger')
        return redirect(url_for('manage_marks'))

    students = Student.query.filter_by(
        school_id=sid, class_name=meta['class_name'], active=1
    ).all()
    by_adm = {(s.adm_number or '').strip().lower(): s for s in students}

    saved, unknown = 0, []
    for row in rows:
        key = row['adm_number'].lower()
        stud = by_adm.get(key)
        if not stud:
            unknown.append(row['adm_number'])
            continue
        score = row['score']
        ex = Mark.query.filter_by(
            student_id=stud.id,
            subject_id=subj.id,
            exam_type=meta['exam_type'],
            term=meta['term'],
            year=meta['year'],
        ).first()
        if ex:
            ex.score = score
            ex.teacher_id = current_user.id
        else:
            db.session.add(Mark(
                student_id=stud.id,
                subject_id=subj.id,
                teacher_id=current_user.id,
                score=score,
                exam_type=meta['exam_type'],
                term=meta['term'],
                year=meta['year'],
                school_id=sid,
            ))
        saved += 1

    db.session.commit()

    msg = f'Excel import: {saved} mark(s) saved for {subj.name} / {meta["class_name"]} / {meta["exam_type"]}.'
    if unknown:
        msg += f' Unknown adm numbers skipped: {", ".join(unknown[:8])}'
        if len(unknown) > 8:
            msg += f' (+{len(unknown) - 8} more)'
        flash(msg, 'danger' if saved == 0 else 'success')
    else:
        flash(msg, 'success')

    return redirect(url_for(
        'manage_marks',
        subject_id=subj.id,
        class_name=meta['class_name'],
        exam_type=meta['exam_type'],
        term=meta['term'],
        year=meta['year'],
    ))

@app.route('/teacher/timetable', methods=['GET','POST'])
@login_required
def manage_timetable():
    if current_user.role != 'teacher': return redirect(url_for('dashboard'))
    sid = current_user.school_id
    if request.method == 'POST':
        sub_id = int(request.form['subject_id'])
        if not Subject.query.filter_by(id=sub_id, school_id=sid, teacher_id=current_user.id).first():
            flash('You can only schedule subjects assigned to you.', 'danger')
            return redirect(url_for('manage_timetable'))
        tt = Timetable(
            school_id=sid,
            class_name=request.form['class_name'],
            stream=request.form.get('stream', 'A'),
            subject_id=sub_id,
            teacher_id=current_user.id,
            day=request.form['day'],
            start_time=request.form['start_time'],
            end_time=request.form['end_time']
        )
        db.session.add(tt)
        db.session.commit()
        flash('Timetable slot added', 'success')
        return redirect(url_for('manage_timetable'))
    subjects = _assigned_subjects(sid, current_user.id)
    timetable = Timetable.query.filter_by(school_id=sid, teacher_id=current_user.id).all()
    subj_map = {s.id: s for s in Subject.query.filter_by(school_id=sid).all()}
    return render_template('teacher/timetable.html', subjects=subjects, timetable=timetable, subj_map=subj_map)

@app.route('/teacher/timetable/<int:tt_id>/delete')
@login_required
def delete_timetable(tt_id):
    if current_user.role != 'teacher': return redirect(url_for('dashboard'))
    tt = Timetable.query.get_or_404(tt_id)
    if tt.teacher_id != current_user.id:
        flash('You can only delete your own timetable slots.', 'danger')
        return redirect(url_for('manage_timetable'))
    db.session.delete(tt)
    db.session.commit()
    flash('Timetable slot removed.', 'success')
    return redirect(url_for('manage_timetable'))

@app.route('/teacher/results')
@login_required
def teacher_results():
    if current_user.role != 'teacher': return redirect(url_for('dashboard'))
    sid = current_user.school_id
    class_filter = request.args.get('class_name', '')
    term = request.args.get('term', 'Term 1')
    year = int(request.args.get('year', datetime.utcnow().year))

    students_q = Student.query.filter_by(school_id=sid, active=1)
    if class_filter: students_q = students_q.filter_by(class_name=class_filter)
    students = students_q.all()

    subjects = Subject.query.filter_by(school_id=sid).all()
    marks = Mark.query.filter_by(school_id=sid, term=term, year=year).all()
    exam_types = ['CAT 1', 'CAT 2', 'Midterm', 'Final']

    results = {}
    for s in students:
        results[s.id] = {
            'student': s,
            'marks': {subj.id: {} for subj in subjects},
        }
    for m in marks:
        if m.student_id in results and m.subject_id in results[m.student_id]['marks']:
            results[m.student_id]['marks'][m.subject_id][m.exam_type] = m.score

    for r in results.values():
        all_scores = [sc for sm in r['marks'].values() for sc in sm.values()]
        r['avg'] = round(sum(all_scores)/len(all_scores), 1) if all_scores else 0
        r['grade'] = grade(r['avg'])

    sorted_results = sorted(results.values(), key=lambda x: -x['avg'])
    for i, r in enumerate(sorted_results): r['position'] = i+1

    classes = [f'Class {i}' for i in range(1, 8)]
    return render_template('teacher/results.html', results=sorted_results, subjects=subjects,
                           term=term, year=year, class_filter=class_filter, classes=classes,
                           grade=grade, exam_types=exam_types)

@app.route('/teacher/results/pdf')
@login_required
def download_results_pdf():
    if current_user.role != 'teacher': return redirect(url_for('dashboard'))
    sid = current_user.school_id
    class_filter = request.args.get('class_name', '')
    term = request.args.get('term', 'Term 1')
    year = int(request.args.get('year', datetime.utcnow().year))
    school = School.query.get(sid)
    students_q = Student.query.filter_by(school_id=sid, active=1)
    if class_filter: students_q = students_q.filter_by(class_name=class_filter)
    students = students_q.all()
    subjects = Subject.query.filter_by(school_id=sid).all()
    marks = Mark.query.filter_by(school_id=sid, term=term, year=year).all()
    exam_types = ['CAT 1', 'CAT 2', 'Midterm', 'Final']

    results = {}
    for s in students:
        results[s.id] = {'student': s, 'marks': {subj.id: {} for subj in subjects}}
    for m in marks:
        if m.student_id in results and m.subject_id in results[m.student_id]['marks']:
            results[m.student_id]['marks'][m.subject_id][m.exam_type] = m.score
    for r in results.values():
        all_scores = [sc for sm in r['marks'].values() for sc in sm.values()]
        r['avg'] = round(sum(all_scores)/len(all_scores), 1) if all_scores else 0
        r['grade'] = grade(r['avg'])
    sorted_results = sorted(results.values(), key=lambda x: -x['avg'])
    for i, r in enumerate(sorted_results): r['position'] = i+1

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=1.5*cm, bottomMargin=1.5*cm, leftMargin=0.8*cm, rightMargin=0.8*cm)
    story = []
    story.append(Paragraph(school.name.upper(), ParagraphStyle('h', fontSize=14, fontName='Helvetica-Bold', alignment=1, spaceAfter=4)))
    story.append(Paragraph(f'CLASS MARKLIST — {class_filter or "ALL CLASSES"} | {term} {year}', ParagraphStyle('s', fontSize=10, alignment=1, spaceAfter=10)))

    # Build multi-level header
    header_row1 = ['#', 'Student', 'Adm']
    header_row2 = ['', '', '']
    col_w = [0.6*cm, 4.2*cm, 1.8*cm]
    for subj in subjects:
        for et in exam_types:
            header_row1.append(subj.code if et == exam_types[0] else '')
            header_row2.append(et.replace('CAT ','C'))
            col_w.append(0.9*cm)
        header_row1.append('')
        header_row2.append('Avg')
        col_w.append(0.9*cm)
    header_row1 += ['Avg', 'Grd', 'Pos']
    header_row2 += ['', '', '']
    col_w += [0.9*cm, 0.7*cm, 0.6*cm]

    table_data = [header_row1, header_row2]
    for r in sorted_results:
        row = [r['position'], r['student'].full_name[:22], r['student'].adm_number]
        for subj in subjects:
            sm = r['marks'].get(subj.id, {})
            for et in exam_types:
                row.append(sm.get(et, '—'))
            subj_scores = list(sm.values())
            row.append(round(sum(subj_scores)/len(subj_scores),1) if subj_scores else '—')
        row += [r['avg'], r['grade'], r['position']]
        table_data.append(row)

    t = Table(table_data, colWidths=col_w, repeatRows=2)
    style = TableStyle([
        ('BACKGROUND', (0,0), (-1,1), colors.HexColor('#1a6e3c')),
        ('TEXTCOLOR', (0,0), (-1,1), colors.white),
        ('FONTNAME', (0,0), (-1,1), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 7),
        ('GRID', (0,0), (-1,-1), 0.3, colors.HexColor('#aaaaaa')),
        ('ROWBACKGROUNDS', (0,2), (-1,-1), [colors.white, colors.HexColor('#f5fbf7')]),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('ALIGN', (1,2), (1,-1), 'LEFT'),
        ('SPAN', (0,0), (0,1)), ('SPAN', (1,0), (1,1)), ('SPAN', (2,0), (2,1)),
    ])
    # Span subject header cells
    col_idx = 3
    for subj in subjects:
        style.add('SPAN', (col_idx,0), (col_idx+len(exam_types),0))
        col_idx += len(exam_types) + 1
    style.add('SPAN', (col_idx,0),(col_idx,1))
    style.add('SPAN', (col_idx+1,0),(col_idx+1,1))
    style.add('SPAN', (col_idx+2,0),(col_idx+2,1))
    t.setStyle(style)
    story.append(t)
    story.append(Spacer(1, 0.4*cm))
    story.append(Paragraph(f'Generated: {datetime.utcnow().strftime("%d %b %Y %H:%M")} | Teacher: {current_user.full_name}',
        ParagraphStyle('f', fontSize=7, alignment=1, textColor=colors.gray)))
    doc.build(story)
    buf.seek(0)
    resp = make_response(buf.read())
    resp.headers['Content-Type'] = 'application/pdf'
    resp.headers['Content-Disposition'] = f'inline; filename=marklist_{class_filter}_{term}_{year}.pdf'
    return resp

# ─── PARENT ───────────────────────────────────────────────────────────────────

def _latest_mark_period(student_id):
    """Return (term, year) for the most recent marks, or (None, None)."""
    m = (Mark.query.filter_by(student_id=student_id)
         .order_by(Mark.year.desc(), Mark.id.desc()).first())
    if not m:
        return None, None
    return m.term, m.year

def _student_term_progress(student, term, year):
    """Compute average, grade, and per-subject progress for one child/period."""
    subjects = Subject.query.filter_by(school_id=student.school_id).order_by(Subject.name).all()
    marks = Mark.query.filter_by(student_id=student.id, term=term, year=year).all()
    exam_types = ['CAT 1', 'CAT 2', 'Midterm', 'Final']
    by_subj = {s.id: {} for s in subjects}
    for m in marks:
        if m.subject_id in by_subj:
            by_subj[m.subject_id][m.exam_type] = m.score

    subject_rows = []
    all_scores = []
    for s in subjects:
        scores = list(by_subj[s.id].values())
        if not scores:
            continue
        sub_avg = round(sum(scores) / len(scores), 1)
        all_scores.extend(scores)
        subject_rows.append({
            'subject': s,
            'avg': sub_avg,
            'grade': grade(sub_avg),
            'exams_done': len(scores),
            'exams_total': len(exam_types),
            'scores': by_subj[s.id],
        })

    overall = round(sum(all_scores) / len(all_scores), 1) if all_scores else None
    return {
        'term': term,
        'year': year,
        'avg': overall,
        'grade': grade(overall) if overall is not None else None,
        'marks_count': len(all_scores),
        'subjects_with_marks': len(subject_rows),
        'subjects_total': len(subjects),
        'subject_rows': subject_rows,
        'exam_types': exam_types,
    }

@app.route('/parent')
@login_required
def parent_dashboard():
    if current_user.role != 'parent': return redirect(url_for('dashboard'))
    children = Student.query.filter_by(parent_id=current_user.id, active=1).all()
    if not children:
        return redirect(url_for('parent_enroll'))
    children_progress = []
    for child in children:
        term, year = _latest_mark_period(child.id)
        if term and year:
            prog = _student_term_progress(child, term, year)
        else:
            prog = {
                'term': None, 'year': None, 'avg': None, 'grade': None,
                'marks_count': 0, 'subjects_with_marks': 0, 'subjects_total': 0,
                'subject_rows': [], 'exam_types': [],
            }
        children_progress.append({'child': child, 'progress': prog})
    return render_template('parent/dashboard.html', children_progress=children_progress)

@app.route('/parent/enroll', methods=['GET', 'POST'])
@login_required
def parent_enroll():
    """Parent registers a child for school admin approval."""
    if current_user.role != 'parent':
        return redirect(url_for('dashboard'))
    if not current_user.school_id:
        flash('Your account is not linked to a school. Update your profile or contact the school.', 'danger')
        return redirect(url_for('parent_profile'))

    school = School.query.get(current_user.school_id)
    children = Student.query.filter_by(parent_id=current_user.id, active=1).all()
    requests = StudentEnrollmentRequest.query.filter_by(
        parent_id=current_user.id
    ).order_by(StudentEnrollmentRequest.created_at.desc()).all()
    pending = [r for r in requests if r.status == 'pending']

    if request.method == 'POST':
        if pending:
            flash('You already have a pending application. Please wait for the school to review it.', 'warning')
            return redirect(url_for('parent_enroll'))

        full_name = request.form.get('full_name', '').strip()
        if not full_name:
            flash('Full name is required.', 'danger')
            return redirect(url_for('parent_enroll'))

        duplicate = StudentEnrollmentRequest.query.filter_by(
            parent_id=current_user.id, full_name=full_name, status='pending'
        ).first()
        if duplicate:
            flash('An application for this child is already pending.', 'warning')
            return redirect(url_for('parent_enroll'))

        req = StudentEnrollmentRequest(
            parent_id=current_user.id,
            school_id=current_user.school_id,
            full_name=full_name,
            class_name='Class 1',
            stream='',
            dob=request.form.get('dob', '') or '',
            gender=request.form.get('gender', '') or '',
            parent_notes=request.form.get('parent_notes', '').strip() or None,
            status='pending',
        )
        db.session.add(req)
        db.session.commit()
        flash(
            f'Application for {full_name} submitted. The school admin will review and respond.',
            'success'
        )
        return redirect(url_for('parent_enroll'))

    return render_template(
        'parent/enroll.html',
        school=school,
        children=children,
        requests=requests,
        pending=pending,
        can_submit=not pending,
    )

@app.route('/parent/results/<int:student_id>')
@login_required
def parent_results(student_id):
    if current_user.role != 'parent': return redirect(url_for('dashboard'))
    student = Student.query.get_or_404(student_id)
    if student.parent_id != current_user.id: return redirect(url_for('parent_dashboard'))
    sid = student.school_id

    # Default to the latest period that has marks (seeded data may not be current year)
    latest_term, latest_year = _latest_mark_period(student.id)
    term = request.args.get('term') or latest_term or 'Term 1'
    try:
        year = int(request.args.get('year') or latest_year or datetime.utcnow().year)
    except (TypeError, ValueError):
        year = latest_year or datetime.utcnow().year

    marks = Mark.query.filter_by(student_id=student.id, term=term, year=year).all()
    subjects = Subject.query.filter_by(school_id=sid).order_by(Subject.name).all()
    exam_types = ['CAT 1', 'CAT 2', 'Midterm', 'Final']
    # Build grid: {subject_id: {exam_type: score}}
    mark_grid = {s.id: {} for s in subjects}
    for m in marks:
        if m.subject_id in mark_grid:
            mark_grid[m.subject_id][m.exam_type] = m.score
    all_scores = [sc for sm in mark_grid.values() for sc in sm.values()]
    avg = round(sum(all_scores)/len(all_scores), 1) if all_scores else 0
    progress = _student_term_progress(student, term, year)
    invoices = Invoice.query.filter_by(student_id=student.id).all()
    return render_template('parent/results.html', student=student,
        subjects=subjects, mark_grid=mark_grid,
        term=term, year=year, grade=grade,
        invoices=invoices, exam_types=exam_types, avg=avg, progress=progress)

@app.route('/parent/profile', methods=['GET', 'POST'])
@login_required
def parent_profile():
    """Allow a parent to update their own profile and password."""
    if current_user.role != 'parent':
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        full_name = request.form.get('full_name', '').strip()
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip() or None
        phone = request.form.get('phone', '').strip() or None
        current_pw = request.form.get('current_password', '').strip()
        new_pw = request.form.get('new_password', '').strip()
        confirm_pw = request.form.get('confirm_password', '').strip()

        if not full_name or not username:
            flash('Full name and username are required.', 'danger')
            return redirect(url_for('parent_profile'))

        clash = User.query.filter(User.username == username, User.id != current_user.id).first()
        if clash:
            flash('Username already taken. Please choose another.', 'danger')
            return redirect(url_for('parent_profile'))

        # Password change is optional but must be authenticated
        if new_pw or confirm_pw:
            if not current_pw or not current_user.check_password(current_pw):
                flash('Current password is incorrect.', 'danger')
                return redirect(url_for('parent_profile'))
            if len(new_pw) < 6:
                flash('New password must be at least 6 characters.', 'danger')
                return redirect(url_for('parent_profile'))
            if new_pw != confirm_pw:
                flash('New passwords do not match.', 'danger')
                return redirect(url_for('parent_profile'))
            current_user.set_password(new_pw)

        current_user.full_name = full_name
        current_user.username = username
        current_user.email = email
        current_user.phone = phone
        db.session.commit()
        flash('Profile updated successfully.', 'success')
        return redirect(url_for('parent_profile'))

    school = School.query.get(current_user.school_id) if current_user.school_id else None
    return render_template('parent/profile.html', school=school)

def _serialize_parent_pay_record(row):
    """JSON-safe payment row for parent student payment modal."""
    pay = row.get('payment')
    invoice_id = row.get('invoice_id')
    can_generate = row.get('can_generate', False)
    return {
        'type': row['type'],
        'date': row['date'].strftime('%d %b %Y') if row.get('date') else '—',
        'ref': row['ref'],
        'description': row['description'],
        'term': row.get('term', ''),
        'amount': row['amount'],
        'status': row['status'],
        'pay_id': pay.id if pay else None,
        'invoice_id': invoice_id,
        'can_generate': can_generate,
        'control_number': row.get('control_number'),
        'generate_url': url_for('parent_generate_control_number', inv_id=invoice_id) if can_generate and invoice_id else None,
    }

def _build_student_payment_summaries(students, all_records):
    """Per-child payment totals and records for the parent payments modal."""
    summaries = []
    by_id = {}
    for sid, student in sorted(students.items(), key=lambda x: x[1].full_name):
        records = [
            _serialize_parent_pay_record(r) for r in all_records
            if r.get('student') and r['student'].id == sid
        ]
        debt = sum(r['amount'] for r in records if r['type'] == 'debt')
        paid = sum(r['amount'] for r in records if r['type'] == 'completed')
        pending_count = sum(1 for r in records if r['type'] == 'pending')
        entry = {
            'id': sid,
            'name': student.full_name,
            'adm': student.adm_number,
            'class_name': f'{student.class_name} {student.stream}',
            'debt': debt,
            'paid': paid,
            'pending_count': pending_count,
            'records': records,
        }
        summaries.append(entry)
        by_id[str(sid)] = entry
    return summaries, by_id

@app.route('/parent/payments')
@login_required
def parent_payments():
    """Parent: view debts, pending and completed payments for linked children."""
    if current_user.role != 'parent':
        return redirect(url_for('dashboard'))

    children = Student.query.filter_by(parent_id=current_user.id, active=1).order_by(Student.full_name).all()
    students = {s.id: s for s in children}
    child_ids = list(students.keys())

    if not child_ids:
        return render_template(
            'parent/payments.html',
            students=students,
            student_summaries=[],
            student_pay_json='{}',
            all_records=[],
            total_debt=0,
            total_pending=0,
            total_paid=0,
            debt_count=0,
            pending_count=0,
            completed_count=0,
        )

    invoices_list = Invoice.query.filter(Invoice.student_id.in_(child_ids)).order_by(Invoice.created_at.desc()).all()
    invoices = {i.id: i for i in invoices_list}
    inv_ids = list(invoices.keys())

    payments = []
    if inv_ids:
        payments = Payment.query.filter(Payment.invoice_id.in_(inv_ids)).order_by(Payment.paid_at.desc()).all()

    pending = [p for p in payments if not p.receipt_number]
    completed = [p for p in payments if p.receipt_number]
    pending_by_invoice = {p.invoice_id: p for p in pending}

    # Amount paid per invoice (completed payments only)
    paid_by_invoice = {}
    for p in completed:
        paid_by_invoice[p.invoice_id] = paid_by_invoice.get(p.invoice_id, 0) + (p.amount_paid or 0)

    debts = []
    for inv in invoices_list:
        paid = paid_by_invoice.get(inv.id, 0)
        balance = max(0, (inv.amount or 0) - paid)
        if balance > 0:
            debts.append({
                'invoice': inv,
                'student': students.get(inv.student_id),
                'paid': paid,
                'balance': balance,
            })

    total_debt = sum(d['balance'] for d in debts)
    total_pending = len(pending)
    total_paid = sum(p.amount_paid or 0 for p in completed)

    # One row per invoice — control number saved once on the row when generated
    all_records = []
    debt_count = 0
    for inv in invoices_list:
        paid = paid_by_invoice.get(inv.id, 0)
        balance = max(0, (inv.amount or 0) - paid)
        pending_pay = pending_by_invoice.get(inv.id)
        student = students.get(inv.student_id)

        if pending_pay:
            all_records.append({
                'type': 'pending',
                'date': pending_pay.paid_at or inv.created_at,
                'student': student,
                'ref': inv.invoice_number,
                'control_number': pending_pay.control_number,
                'description': inv.description,
                'term': f'{inv.term} {inv.year}',
                'amount': balance if balance > 0 else inv.amount,
                'paid': paid,
                'invoiced': inv.amount,
                'status': 'pending',
                'payment': pending_pay,
                'invoice_id': inv.id,
                'can_generate': False,
            })
        elif balance > 0:
            debt_count += 1
            all_records.append({
                'type': 'debt',
                'date': inv.created_at,
                'student': student,
                'ref': inv.invoice_number,
                'control_number': None,
                'description': inv.description,
                'term': f'{inv.term} {inv.year}',
                'amount': balance,
                'paid': paid,
                'invoiced': inv.amount,
                'status': inv.status,
                'payment': None,
                'invoice_id': inv.id,
                'can_generate': True,
            })

    for p in completed:
        inv = invoices.get(p.invoice_id)
        all_records.append({
            'type': 'completed',
            'date': p.paid_at,
            'student': students.get(inv.student_id) if inv else None,
            'ref': p.receipt_number,
            'description': inv.description if inv else '—',
            'term': f'{inv.term} {inv.year}' if inv else '',
            'amount': p.amount_paid or 0,
            'paid': p.amount_paid or 0,
            'invoiced': inv.amount if inv else 0,
            'status': 'paid',
            'payment': p,
        })
    all_records.sort(key=lambda r: r['date'] or datetime.min, reverse=True)
    student_summaries, student_pay_by_id = _build_student_payment_summaries(students, all_records)

    return render_template(
        'parent/payments.html',
        students=students,
        student_summaries=student_summaries,
        student_pay_json=json.dumps(student_pay_by_id),
        all_records=all_records,
        total_debt=total_debt,
        total_pending=total_pending,
        total_paid=total_paid,
        debt_count=debt_count,
        pending_count=total_pending,
        completed_count=len(completed),
    )

@app.route('/parent/control-number/<int:inv_id>')
@login_required
def parent_generate_control_number(inv_id):
    """Parent: generate a control number to pay an outstanding invoice."""
    if current_user.role != 'parent':
        return redirect(url_for('dashboard'))

    inv = Invoice.query.get_or_404(inv_id)
    student = Student.query.get(inv.student_id)
    if not student or student.parent_id != current_user.id:
        flash('You can only generate control numbers for your children.', 'danger')
        return redirect(url_for('parent_payments'))

    existing_before = _pending_payment_for_invoice(inv.id)
    pay, err = _issue_control_number(inv, current_user.id)
    if err:
        flash(err, 'warning')
        return redirect(url_for('parent_payments', view='completed'))

    school = School.query.get(inv.school_id)
    if existing_before:
        flash(f'Control number already issued: {pay.control_number}', 'info')
    else:
        if school:
            notify_result = notifications.notify_control_number(
                current_user, student, inv, pay.control_number, school
            )
            extra = notifications.summarize_channels(notify_result)
            if extra:
                flash(f'Control number generated: {pay.control_number} (sent via {extra})', 'success')
            else:
                flash(f'Control number generated: {pay.control_number}', 'success')
        else:
            flash(f'Control number generated: {pay.control_number}', 'success')
    return redirect(url_for('parent_payments', view='all'))

# ─── ANNOUNCEMENTS ────────────────────────────────────────────────────────────

@app.route('/announcements')
@login_required
def announcements():
    role = current_user.role
    sid = current_user.school_id
    q = Announcement.query
    if role == 'sysadmin':
        q = q.filter_by(target_role='sysadmin')
    else:
        q = q.filter((Announcement.target_role == role) | (Announcement.target_role == 'all'))
        if sid:
            q = q.filter((Announcement.school_id == sid) | (Announcement.school_id.is_(None)))
    all_announcements = q.order_by(Announcement.created_at.desc()).all()
    return render_template('announcements.html', announcements=all_announcements)

@app.route('/announcements/create', methods=['POST'])
@login_required
def create_announcement():
    if current_user.role == 'parent':
        flash('Parents cannot post announcements.', 'danger')
        return redirect(url_for('announcements'))
    target_role = request.form['target_role']
    a = Announcement(
        title=request.form['title'],
        content=request.form['content'],
        author_id=current_user.id,
        target_role=target_role,
        school_id=current_user.school_id if current_user.role != 'sysadmin' else None
    )
    db.session.add(a)
    db.session.commit()

    # Email (and SMS when configured) matching audience
    q = User.query.filter(
        User.active == 1,
        db.or_(
            db.and_(User.email.isnot(None), User.email != ''),
            db.and_(User.phone.isnot(None), User.phone != ''),
        ),
    )
    if target_role != 'all':
        q = q.filter_by(role=target_role)
    if a.school_id:
        q = q.filter((User.school_id == a.school_id) | (User.role == 'sysadmin'))
    recipients = q.all()
    result = notifications.notify_announcement(recipients, a, _school_name(a.school_id))
    channels = notifications.summarize_channels({
        'email': bool(result.get('email')),
        'sms': bool(result.get('sms')),
    })
    emailed = result.get('email') or 0
    if channels:
        detail = f' ({emailed} email(s))' if emailed else ''
        flash(f'Announcement posted! Sent via {channels}{detail}.', 'success')
    else:
        flash('Announcement posted! (No emails sent — check Gmail settings / recipient emails.)', 'success')
    return redirect(url_for('announcements'))

@app.route('/announcements/<int:aid>/delete')
@login_required
def delete_announcement(aid):
    a = Announcement.query.get_or_404(aid)
    if a.author_id != current_user.id and current_user.role != 'sysadmin':
        flash('You can only delete your own announcements.', 'danger')
        return redirect(url_for('announcements'))
    db.session.delete(a)
    db.session.commit()
    flash('Announcement deleted.', 'success')
    return redirect(url_for('announcements'))

# ─── SEED ─────────────────────────────────────────────────────────────────────

def seed():
    db.create_all()
    if User.query.filter_by(username='admin').first(): return
    sa = User(username='admin', full_name='System Administrator', email='admin@elimu.tz', role='sysadmin')
    sa.set_password('admin123')
    db.session.add(sa)
    school = School(name='Elimu Primary School', reg_number='S0001', address='Dar es Salaam, Tanzania', phone='+255 700 000 001', email='info@elimu.ac.tz')
    db.session.add(school)
    db.session.flush()
    users_data = [
        ('schooladmin', 'schooladmin', 'School Admin', 'schooladmin@elimu.ac.tz', 'admin123'),
        ('accountant', 'accountant', 'Jane Accountant', 'accounts@elimu.ac.tz', 'acc123'),
        ('teacher1', 'teacher', 'Mr. John Mwalimu', 'john@elimu.ac.tz', 'teacher123'),
        ('teacher2', 'teacher', 'Ms. Amina Ally', 'amina@elimu.ac.tz', 'teacher123'),
        ('parent1', 'parent', 'Mr. Ali Hassan', 'ali@gmail.com', 'parent123'),
        ('parent2', 'parent', 'Mrs. Fatuma Juma', 'fatuma@gmail.com', 'parent123'),
    ]
    created = {}
    for uname, role, fname, email, pw in users_data:
        u = User(username=uname, full_name=fname, email=email, role=role, school_id=school.id)
        u.set_password(pw)
        db.session.add(u)
        created[uname] = u
    db.session.flush()
    subjects_data = [
        ('Mathematics','MATH'), ('English','ENG'), ('Kiswahili','KSW'), ('Science','SCI'),
        ('Social Studies','SST'), ('Civic & Moral Education','CME'), ('Vocational Skills','VS'),
        ('ICT','ICT'), ('French','FRE'), ('Geography','GEO')
    ]
    subjs = []
    for name, code in subjects_data:
        subj = Subject(name=name, code=code, school_id=school.id, teacher_id=created['teacher1'].id if len(subjs) < 5 else created['teacher2'].id)
        db.session.add(subj)
        subjs.append(subj)
    db.session.flush()
    students_data = [
        ('Baraka Juma', 'Class 1', 'A', 'M', created['parent1'].id),
        ('Salma Hassan', 'Class 1', 'A', 'F', created['parent1'].id),
        ('Omari Rashid', 'Class 2', 'B', 'M', created['parent2'].id),
        ('Zainab Musa', 'Class 2', 'A', 'F', created['parent2'].id),
        ('Patrick Lema', 'Class 3', 'A', 'M', None),
        ('Grace Msangi', 'Class 3', 'B', 'F', None),
        ('John Doe', 'Class 7', 'A', 'M', None),
    ]
    studs = []
    for i, (name, cls, stream, gender, pid) in enumerate(students_data):
        s = Student(full_name=name, adm_number=f'ADM{1000+i}', class_name=cls, stream=stream, gender=gender, parent_id=pid, school_id=school.id)
        db.session.add(s)
        studs.append(s)
    db.session.flush()
    for stud in studs:
        for subj in subjs:
            for exam_type in ['CAT 1', 'CAT 2', 'Midterm', 'Final']:
                score = round(random.uniform(35, 98), 1)
                m = Mark(student_id=stud.id, subject_id=subj.id, teacher_id=subj.teacher_id,
                         score=score, exam_type=exam_type, term='Term 1', year=datetime.utcnow().year, school_id=school.id)
                db.session.add(m)
    days = ['Monday','Tuesday','Wednesday','Thursday','Friday']
    times = [('07:00','08:00'),('08:00','09:00'),('09:00','10:00'),('10:30','11:30'),('11:30','12:30')]
    for i, subj in enumerate(subjs):
        tt = Timetable(school_id=school.id, class_name='Class 1', stream='A',
                       subject_id=subj.id, teacher_id=subj.teacher_id,
                       day=days[i%5], start_time=times[i%5][0], end_time=times[i%5][1])
        db.session.add(tt)
    for i, stud in enumerate(studs[:4]):
        inv = Invoice(invoice_number=f'INV{2000+i}', student_id=stud.id, school_id=school.id,
                      amount=350000, description='Tuition Fee Term 1', term='Term 1', year=datetime.utcnow().year,
                      status='paid' if i < 2 else 'unpaid')
        db.session.add(inv)
        db.session.flush()
        if i < 2:
            pay = Payment(invoice_id=inv.id, control_number=f'CTR{9000+i}',
                          amount_paid=350000, payment_method='M-Pesa',
                          receipt_number=f'RCP{5000+i}', school_id=school.id, created_by=created['accountant'].id)
            db.session.add(pay)
    db.session.commit()
    print("✅ Database seeded successfully!")

# ─── SETUP / DIAGNOSTIC ───────────────────────────────────────────────────────

@app.route('/setup')
def setup_page():
    ok = _init_db()
    if ok:
        admin = User.query.filter_by(username='admin').first()
        return f'''
        <html><body style="font-family:sans-serif;padding:40px;background:#f8fafc">
        <h2 style="color:#1a6e3c">✅ Elimu System — DB Ready</h2>
        <p>Admin user: <strong>{admin.full_name if admin else "NOT FOUND"}</strong></p>
        <p>Users: {User.query.count()} | Schools: {School.query.count()} | Students: {Student.query.count()}</p>
        <p><a href="/" style="color:#1a6e3c;font-weight:700">→ Go to Login</a></p>
        <p><a href="/health" style="color:#6b7280">→ Health Check</a></p>
        </body></html>
        '''
    return '<html><body style="font-family:sans-serif;padding:40px"><h2 style="color:#c0392b">❌ DB Setup Failed</h2><p>Check Vercel runtime logs for details.</p></body></html>', 500

@app.route('/health')
def health():
    try:
        with app.app_context():
            db.create_all()
            _ensure_user_phone_column()
            ok = User.query.first() is not None
        return {'status': 'ok', 'db': 'connected' if ok else 'empty'}
    except Exception as e:
        return {'status': 'error', 'db': str(e)}, 500

@app.errorhandler(500)
def handle_500(e):
    return '<html><body style="font-family:sans-serif;padding:40px;background:#fef2f2"><h2 style="color:#c0392b">❌ Internal Server Error</h2><p>Try <a href="/setup" style="color:#1a6e3c">/setup</a> to initialize the database, or check Vercel runtime logs.</p></body></html>', 500

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        _ensure_user_phone_column()
        if not User.query.filter_by(username='admin').first():
            seed()
    app.run(debug=True, port=5000)
