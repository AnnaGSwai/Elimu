"""
Email notification service — Gmail SMTP.

Credentials must come from environment variables (never hardcode):
  GMAIL_USER          — sender Gmail address
  GMAIL_APP_PASSWORD  — Google App Password (not the account password)
  MAIL_FROM_NAME      — optional display name (default: Elimu)

Requires a Google App Password:
  Google Account → Security → 2-Step Verification → App passwords
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

# Gmail SMTP settings
SMTP_HOST = 'smtp.gmail.com'
SMTP_PORT = 587


def is_configured() -> bool:
    """Return True when Gmail credentials are present."""
    return bool(os.environ.get('GMAIL_USER') and os.environ.get('GMAIL_APP_PASSWORD'))


def _from_header() -> str:
    user = os.environ.get('GMAIL_USER', '').strip()
    name = os.environ.get('MAIL_FROM_NAME', 'Elimu').strip() or 'Elimu'
    return f'{name} <{user}>'


def send_email(
    to: str,
    subject: str,
    body_text: str,
    body_html: Optional[str] = None,
) -> bool:
    """
    Send one email via Gmail SMTP.

    Returns True on success, False on skip/failure (never raises to callers).
    """
    to = (to or '').strip()
    if not to:
        logger.debug('Email skipped: empty recipient')
        return False

    if not is_configured():
        logger.warning('Email skipped: GMAIL_USER / GMAIL_APP_PASSWORD not set')
        return False

    user = os.environ['GMAIL_USER'].strip()
    password = os.environ['GMAIL_APP_PASSWORD'].strip()

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = _from_header()
    msg['To'] = to
    msg.attach(MIMEText(body_text, 'plain', 'utf-8'))
    if body_html:
        msg.attach(MIMEText(body_html, 'html', 'utf-8'))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(user, password)
            server.sendmail(user, [to], msg.as_string())
        logger.info('Email sent to %s — %s', to, subject)
        return True
    except Exception as ex:
        # Do not expose credentials or full SMTP details to end users
        logger.error('Email failed to %s: %s', to, type(ex).__name__)
        return False


def send_bulk(recipients: Iterable[str], subject: str, body_text: str,
              body_html: Optional[str] = None) -> int:
    """Send the same message to many addresses. Returns count of successes."""
    seen = set()
    sent = 0
    for addr in recipients:
        addr = (addr or '').strip().lower()
        if not addr or addr in seen:
            continue
        seen.add(addr)
        if send_email(addr, subject, body_text, body_html):
            sent += 1
    return sent


# ─── Domain helpers (used by controllers) ─────────────────────────────────────

def notify_account_created(user, school_name: Optional[str] = None) -> bool:
    """Welcome email after account creation. Never includes the password."""
    if not user or not getattr(user, 'email', None):
        return False
    school = school_name or 'your school'
    subject = 'Welcome to Elimu — account created'
    text = (
        f'Hello {user.full_name or user.username},\n\n'
        f'Your Elimu account has been created for {school}.\n\n'
        f'Username: {user.username}\n'
        f'Role: {user.role}\n\n'
        f'Log in with the password provided by your school administrator '
        f'(or the one you chose during registration).\n\n'
        f'— Elimu School Management\n'
    )
    html = f"""
    <p>Hello <strong>{user.full_name or user.username}</strong>,</p>
    <p>Your Elimu account has been created for <strong>{school}</strong>.</p>
    <ul>
      <li><strong>Username:</strong> {user.username}</li>
      <li><strong>Role:</strong> {user.role}</li>
    </ul>
    <p>Log in with the password provided by your school administrator
       (or the one you chose during registration).</p>
    <p>— Elimu School Management</p>
    """
    return send_email(user.email, subject, text, html)


def notify_invoice_created(parent, student, invoice, school) -> bool:
    """Notify parent when a fee invoice is issued."""
    if not parent or not getattr(parent, 'email', None):
        return False
    school_name = school.name if school else 'School'
    subject = f'New fee invoice — {invoice.invoice_number}'
    text = (
        f'Hello {parent.full_name or parent.username},\n\n'
        f'A new fee invoice has been issued for {student.full_name} '
        f'({student.adm_number}) at {school_name}.\n\n'
        f'Invoice: {invoice.invoice_number}\n'
        f'Description: {invoice.description}\n'
        f'Amount: TZS {invoice.amount:,.0f}\n'
        f'Term / Year: {invoice.term} {invoice.year}\n'
        f'Status: {invoice.status}\n\n'
        f'Please log in to Elimu to view payment details.\n\n'
        f'— {school_name}\n'
    )
    html = f"""
    <p>Hello <strong>{parent.full_name or parent.username}</strong>,</p>
    <p>A new fee invoice has been issued for
       <strong>{student.full_name}</strong> ({student.adm_number})
       at <strong>{school_name}</strong>.</p>
    <ul>
      <li><strong>Invoice:</strong> {invoice.invoice_number}</li>
      <li><strong>Description:</strong> {invoice.description}</li>
      <li><strong>Amount:</strong> TZS {invoice.amount:,.0f}</li>
      <li><strong>Term / Year:</strong> {invoice.term} {invoice.year}</li>
      <li><strong>Status:</strong> {invoice.status}</li>
    </ul>
    <p>Please log in to Elimu to view payment details.</p>
    <p>— {school_name}</p>
    """
    return send_email(parent.email, subject, text, html)


def notify_control_number(parent, student, invoice, control_number, school) -> bool:
    """Notify parent when a payment control number is generated."""
    if not parent or not getattr(parent, 'email', None):
        return False
    school_name = school.name if school else 'School'
    subject = f'Payment control number — {control_number}'
    text = (
        f'Hello {parent.full_name or parent.username},\n\n'
        f'A control number has been generated for {student.full_name}.\n\n'
        f'Invoice: {invoice.invoice_number}\n'
        f'Amount: TZS {invoice.amount:,.0f}\n'
        f'Control number: {control_number}\n\n'
        f'Use this number when paying via M-Pesa, bank, or school office.\n\n'
        f'— {school_name}\n'
    )
    html = f"""
    <p>Hello <strong>{parent.full_name or parent.username}</strong>,</p>
    <p>A control number has been generated for
       <strong>{student.full_name}</strong>.</p>
    <ul>
      <li><strong>Invoice:</strong> {invoice.invoice_number}</li>
      <li><strong>Amount:</strong> TZS {invoice.amount:,.0f}</li>
      <li><strong>Control number:</strong> {control_number}</li>
    </ul>
    <p>Use this number when paying via M-Pesa, bank, or school office.</p>
    <p>— {school_name}</p>
    """
    return send_email(parent.email, subject, text, html)


def notify_payment_received(parent, student, invoice, payment, school) -> bool:
    """Notify parent when a payment is recorded."""
    if not parent or not getattr(parent, 'email', None):
        return False
    school_name = school.name if school else 'School'
    subject = f'Payment received — receipt {payment.receipt_number}'
    text = (
        f'Hello {parent.full_name or parent.username},\n\n'
        f'Payment for {student.full_name} has been recorded.\n\n'
        f'Invoice: {invoice.invoice_number}\n'
        f'Amount paid: TZS {payment.amount_paid:,.0f}\n'
        f'Method: {payment.payment_method}\n'
        f'Receipt: {payment.receipt_number}\n'
        f'Invoice status: {invoice.status}\n\n'
        f'You can download the PDF receipt from the parent portal.\n\n'
        f'— {school_name}\n'
    )
    html = f"""
    <p>Hello <strong>{parent.full_name or parent.username}</strong>,</p>
    <p>Payment for <strong>{student.full_name}</strong> has been recorded.</p>
    <ul>
      <li><strong>Invoice:</strong> {invoice.invoice_number}</li>
      <li><strong>Amount paid:</strong> TZS {payment.amount_paid:,.0f}</li>
      <li><strong>Method:</strong> {payment.payment_method}</li>
      <li><strong>Receipt:</strong> {payment.receipt_number}</li>
      <li><strong>Invoice status:</strong> {invoice.status}</li>
    </ul>
    <p>You can download the PDF receipt from the parent portal.</p>
    <p>— {school_name}</p>
    """
    return send_email(parent.email, subject, text, html)


def notify_password_reset_otp(user, otp: str, expires_minutes: int = 10,
                              school_name: Optional[str] = None) -> bool:
    """Email a one-time password reset code to a parent."""
    if not user or not getattr(user, 'email', None) or not otp:
        return False
    school = school_name or 'your school'
    subject = 'Elimu — password reset code'
    text = (
        f'Hello {user.full_name or user.username},\n\n'
        f'You requested to reset your Elimu parent account password.\n\n'
        f'Your verification code: {otp}\n\n'
        f'This code expires in {expires_minutes} minutes. '
        f'If you did not request this, ignore this email.\n\n'
        f'— {school}\n'
    )
    html = f"""
    <p>Hello <strong>{user.full_name or user.username}</strong>,</p>
    <p>You requested to reset your Elimu parent account password.</p>
    <p style="font-size:22px;font-weight:700;letter-spacing:4px">{otp}</p>
    <p>This code expires in <strong>{expires_minutes} minutes</strong>.
       If you did not request this, you can ignore this email.</p>
    <p>— {school}</p>
    """
    return send_email(user.email, subject, text, html)


def notify_password_reset_otp(
    user,
    otp: str,
    expires_minutes: int = 10,
    school_name: Optional[str] = None,
) -> bool:
    """Send a one-time password reset code to the parent's email."""
    if not user or not getattr(user, 'email', None) or not otp:
        return False
    school = school_name or 'Elimu'
    subject = 'Elimu password reset code'
    text = (
        f'Hello {user.full_name or user.username},\n\n'
        f'You requested a password reset for your Elimu parent account ({user.username}).\n\n'
        f'Your verification code is: {otp}\n\n'
        f'This code expires in {expires_minutes} minutes.\n'
        f'If you did not request this, you can ignore this email.\n\n'
        f'— {school}\n'
    )
    html = f"""
    <p>Hello <strong>{user.full_name or user.username}</strong>,</p>
    <p>You requested a password reset for your Elimu parent account
       (<strong>{user.username}</strong>).</p>
    <p style="font-size:28px;font-weight:800;letter-spacing:6px;color:#1a6e3c">{otp}</p>
    <p>This code expires in <strong>{expires_minutes} minutes</strong>.</p>
    <p>If you did not request this, you can ignore this email.</p>
    <p>— {school}</p>
    """
    return send_email(user.email, subject, text, html)


def notify_announcement(recipients, announcement, school_name: Optional[str] = None) -> int:
    """Email an announcement to a list of User objects with emails."""
    school = school_name or 'Elimu'
    subject = f'Announcement: {announcement.title}'
    text = (
        f'{school} announcement\n\n'
        f'{announcement.title}\n\n'
        f'{announcement.content}\n\n'
        f'— Elimu\n'
    )
    html = f"""
    <p><strong>{school}</strong> announcement</p>
    <h3>{announcement.title}</h3>
    <p>{announcement.content}</p>
    <p>— Elimu</p>
    """
    emails = [u.email for u in recipients if getattr(u, 'email', None)]
    return send_bulk(emails, subject, text, html)
