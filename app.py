from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, make_response
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.units import cm
import io, random, string, os

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'elimu-secret-2025')
_db_url = os.environ.get('DATABASE_URL', 'sqlite:////tmp/elimu.db')
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

# Helper: ensure DB is ready
def _init_db():
    try:
        db.create_all()
        if not User.query.filter_by(username='admin').first():
            seed()
        return True
    except Exception as ex:
        print(f"[ELIMU] DB init error: {ex}")
        return False

# ─── MODELS ───────────────────────────────────────────────────────────────────

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # sysadmin, schooladmin, accountant, teacher, parent
    full_name = db.Column(db.String(120))
    email = db.Column(db.String(120))
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
    receipt_number = db.Column(db.String(20), unique=True)
    paid_at = db.Column(db.DateTime, default=datetime.utcnow)
    school_id = db.Column(db.Integer, db.ForeignKey('school.id'))
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'))

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
        role = request.form.get('role', 'parent')
        if role not in ['parent', 'teacher', 'accountant']:
            role = 'parent'
        u = User(
            username=username,
            full_name=request.form['full_name'].strip(),
            email=request.form.get('email','').strip(),
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

# ─── SYSADMIN ─────────────────────────────────────────────────────────────────

@app.route('/sysadmin')
@login_required
def sysadmin_dashboard():
    if current_user.role != 'sysadmin': return redirect(url_for('dashboard'))
    schools = School.query.all()
    users = User.query.all()
    students = Student.query.all()
    return render_template('sysadmin/dashboard.html', schools=schools, users=users, students=students)

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

@app.route('/sysadmin/schools/<int:sid>/toggle')
@login_required
def toggle_school(sid):
    if current_user.role != 'sysadmin': return redirect(url_for('dashboard'))
    s = School.query.get_or_404(sid)
    s.active = 0 if s.active else 1
    db.session.commit()
    flash(f'School {"activated" if s.active else "deactivated"}', 'success')
    return redirect(url_for('manage_schools'))

@app.route('/sysadmin/users', methods=['GET','POST'])
@login_required
def manage_users():
    if current_user.role != 'sysadmin': return redirect(url_for('dashboard'))
    if request.method == 'POST':
        u = User(
            username=request.form['username'],
            full_name=request.form['full_name'],
            email=request.form['email'],
            role=request.form['role'],
            school_id=request.form.get('school_id') or None
        )
        u.set_password(request.form['password'])
        db.session.add(u)
        db.session.commit()
        flash('User created successfully', 'success')
        return redirect(url_for('manage_users'))
    users = User.query.filter(User.role != 'sysadmin').all()
    schools = School.query.filter_by(active=1).all()
    return render_template('sysadmin/users.html', users=users, schools=schools)

@app.route('/sysadmin/users/<int:uid>/toggle')
@login_required
def toggle_user(uid):
    if current_user.role != 'sysadmin': return redirect(url_for('dashboard'))
    u = User.query.get_or_404(uid)
    u.active = 0 if u.active else 1
    db.session.commit()
    flash('User status updated', 'success')
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
        return redirect(url_for('manage_students'))
    students = Student.query.filter_by(school_id=sid).all()
    parents = User.query.filter_by(school_id=sid, role='parent', active=1).all()
    return render_template('schooladmin/students.html', students=students, parents=parents)

@app.route('/schooladmin/subjects', methods=['GET','POST'])
@login_required
def manage_subjects():
    if current_user.role != 'schooladmin': return redirect(url_for('dashboard'))
    sid = current_user.school_id
    if request.method == 'POST':
        subj = Subject(
            name=request.form['name'],
            code=request.form['code'],
            school_id=sid,
            teacher_id=request.form.get('teacher_id') or None
        )
        db.session.add(subj)
        db.session.commit()
        flash('Subject added', 'success')
        return redirect(url_for('manage_subjects'))
    subjects = Subject.query.filter_by(school_id=sid).all()
    teachers = User.query.filter_by(school_id=sid, role='teacher', active=1).all()
    return render_template('schooladmin/subjects.html', subjects=subjects, teachers=teachers)

@app.route('/schooladmin/staff', methods=['GET','POST'])
@login_required
def manage_staff():
    if current_user.role != 'schooladmin': return redirect(url_for('dashboard'))
    sid = current_user.school_id
    if request.method == 'POST':
        role = request.form['role']
        u = User(
            username=request.form['username'],
            full_name=request.form['full_name'],
            email=request.form['email'],
            role=role,
            school_id=sid
        )
        u.set_password(request.form['password'])
        db.session.add(u)
        db.session.commit()
        flash(f'{role.capitalize()} account created', 'success')
        return redirect(url_for('manage_staff'))
    staff = User.query.filter(User.school_id==sid, User.role.in_(['teacher','accountant','parent'])).all()
    return render_template('schooladmin/staff.html', staff=staff)

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

@app.route('/accountant/control-number/<int:inv_id>')
@login_required
def generate_control_number(inv_id):
    if current_user.role != 'accountant': return redirect(url_for('dashboard'))
    inv = Invoice.query.get_or_404(inv_id)
    cn = gen_code('CTR', 10)
    pay = Payment(
        invoice_id=inv.id,
        control_number=cn,
        amount_paid=0,
        payment_method='pending',
        receipt_number='',
        school_id=inv.school_id,
        created_by=current_user.id
    )
    db.session.add(pay)
    db.session.commit()
    flash(f'Control Number generated: {cn}', 'success')
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
    student = Student.query.get(inv.student_id)
    school = School.query.get(pay.school_id)

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
    subjects = Subject.query.filter_by(school_id=sid).all()
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
            existing = Mark.query.filter_by(
                student_id=request.form['student_id'],
                subject_id=request.form['subject_id'],
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
                    subject_id=request.form['subject_id'],
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

    subjects  = Subject.query.filter_by(school_id=sid).all()
    all_studs = Student.query.filter_by(school_id=sid, active=1).all()
    classes   = [f'Class {i}' for i in range(1, 8)]

    marklist_students = []
    existing_marks    = {}
    if sel_subject and sel_class:
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

@app.route('/teacher/timetable', methods=['GET','POST'])
@login_required
def manage_timetable():
    if current_user.role != 'teacher': return redirect(url_for('dashboard'))
    sid = current_user.school_id
    if request.method == 'POST':
        tt = Timetable(
            school_id=sid,
            class_name=request.form['class_name'],
            stream=request.form.get('stream', 'A'),
            subject_id=request.form['subject_id'],
            teacher_id=current_user.id,
            day=request.form['day'],
            start_time=request.form['start_time'],
            end_time=request.form['end_time']
        )
        db.session.add(tt)
        db.session.commit()
        flash('Timetable slot added', 'success')
        return redirect(url_for('manage_timetable'))
    subjects = Subject.query.filter_by(school_id=sid).all()
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

@app.route('/parent')
@login_required
def parent_dashboard():
    if current_user.role != 'parent': return redirect(url_for('dashboard'))
    children = Student.query.filter_by(parent_id=current_user.id, active=1).all()
    return render_template('parent/dashboard.html', children=children)

@app.route('/parent/results/<int:student_id>')
@login_required
def parent_results(student_id):
    if current_user.role != 'parent': return redirect(url_for('dashboard'))
    student = Student.query.get_or_404(student_id)
    if student.parent_id != current_user.id: return redirect(url_for('parent_dashboard'))
    sid = student.school_id
    term = request.args.get('term', 'Term 1')
    year = int(request.args.get('year', datetime.utcnow().year))
    marks = Mark.query.filter_by(student_id=student.id, term=term, year=year).all()
    subjects = Subject.query.filter_by(school_id=sid).all()
    exam_types = ['CAT 1', 'CAT 2', 'Midterm', 'Final']
    # Build grid: {subject_id: {exam_type: score}}
    mark_grid = {s.id: {} for s in subjects}
    for m in marks:
        if m.subject_id in mark_grid:
            mark_grid[m.subject_id][m.exam_type] = m.score
    all_scores = [sc for sm in mark_grid.values() for sc in sm.values()]
    avg = round(sum(all_scores)/len(all_scores), 1) if all_scores else 0
    invoices = Invoice.query.filter_by(student_id=student.id).all()
    return render_template('parent/results.html', student=student,
        subjects=subjects, mark_grid=mark_grid,
        term=term, year=year, grade=grade,
        invoices=invoices, exam_types=exam_types, avg=avg)

# ─── SEED ─────────────────────────────────────────────────────────────────────

def seed():
    db.create_all()
    if User.query.filter_by(username='admin').first(): return
    sa = User(username='admin', full_name='System Administrator', email='admin@elimu.tz', role='sysadmin')
    sa.set_password('admin123')
    db.session.add(sa)
    school = School(name='Elimu Secondary School', reg_number='S0001', address='Dar es Salaam, Tanzania', phone='+255 700 000 001', email='info@elimu.ac.tz')
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
                         score=score, exam_type=exam_type, term='Term 1', year=2025, school_id=school.id)
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
                      amount=350000, description='Tuition Fee Term 1', term='Term 1', year=2025,
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
        if not User.query.filter_by(username='admin').first():
            seed()
    app.run(debug=True, port=5000)
