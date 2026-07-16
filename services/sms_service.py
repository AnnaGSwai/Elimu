"""
SMS notification service — Africa's Talking.

Credentials from environment (never hardcode):
  AT_USERNAME   — app username ('sandbox' for test env)
  AT_API_KEY    — Africa's Talking API key
  AT_SENDER_ID  — optional registered shortcode / alphanumeric sender

Docs: https://developers.africastalking.com/docs/sms/sending/bulk
"""

from __future__ import annotations

import logging
import os
import re
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

_initialized = False


def is_configured() -> bool:
    """Return True when Africa's Talking credentials are present."""
    return bool(os.environ.get('AT_API_KEY') and os.environ.get('AT_USERNAME'))


def normalize_phone(phone: Optional[str], default_country: str = '255') -> Optional[str]:
    """
    Normalize a phone number to E.164-ish form for Africa's Talking.

    Accepts: +2557..., 2557..., 07..., 7...
    Returns None if empty / invalid.
    """
    if not phone:
        return None
    digits = re.sub(r'[^\d+]', '', phone.strip())
    if not digits:
        return None
    if digits.startswith('+'):
        digits = digits[1:]
    if digits.startswith('0') and len(digits) >= 9:
        digits = default_country + digits[1:]
    elif len(digits) == 9 and digits[0] in '67':
        # Local mobile without leading 0 (e.g. 712345678)
        digits = default_country + digits
    if not digits.startswith(default_country) and len(digits) < 11:
        return None
    return '+' + digits


def _ensure_client():
    """Initialize the Africa's Talking SDK once per process."""
    global _initialized
    if _initialized:
        return True
    if not is_configured():
        logger.warning('SMS skipped: AT_USERNAME / AT_API_KEY not set')
        return False
    try:
        import africastalking
        africastalking.initialize(
            os.environ['AT_USERNAME'].strip(),
            os.environ['AT_API_KEY'].strip(),
        )
        _initialized = True
        return True
    except Exception as ex:
        logger.error('Africa\'s Talking init failed: %s', type(ex).__name__)
        return False


def send_sms(to: str, message: str) -> bool:
    """
    Send one SMS. Returns True on API accept, False on skip/failure.
    Never raises to callers.
    """
    number = normalize_phone(to)
    if not number:
        logger.debug('SMS skipped: invalid phone %r', to)
        return False
    message = (message or '').strip()
    if not message:
        return False
    if not _ensure_client():
        return False

    try:
        import africastalking
        sms = africastalking.SMS
        sender = os.environ.get('AT_SENDER_ID', '').strip() or None
        # Sandbox ignores custom sender; production uses registered sender ID
        if sender:
            response = sms.send(message, [number], sender_id=sender)
        else:
            response = sms.send(message, [number])
        logger.info('SMS to %s — response: %s', number, response)
        # Check recipient status when present
        try:
            recipients = response.get('SMSMessageData', {}).get('Recipients', [])
            if recipients:
                status = str(recipients[0].get('status', '')).upper()
                return status in ('SUCCESS', 'SENT', 'QUEUED') or 'success' in status.lower()
        except Exception:
            pass
        return True
    except Exception as ex:
        logger.error('SMS failed to %s: %s', number, type(ex).__name__)
        return False


def send_bulk(phones: Iterable[str], message: str) -> int:
    """Send the same SMS to many numbers. Returns success count."""
    sent = 0
    seen = set()
    for phone in phones:
        n = normalize_phone(phone)
        if not n or n in seen:
            continue
        seen.add(n)
        if send_sms(n, message):
            sent += 1
    return sent


# ─── Domain helpers ───────────────────────────────────────────────────────────

def notify_invoice_created(parent, student, invoice, school) -> bool:
    if not parent or not getattr(parent, 'phone', None):
        return False
    school_name = school.name if school else 'School'
    msg = (
        f'{school_name}: New invoice {invoice.invoice_number} for '
        f'{student.full_name}. Amount TZS {invoice.amount:,.0f}. '
        f'{invoice.description or ""}'.strip()
    )
    return send_sms(parent.phone, msg[:320])


def notify_control_number(parent, student, invoice, control_number, school) -> bool:
    if not parent or not getattr(parent, 'phone', None):
        return False
    school_name = school.name if school else 'School'
    msg = (
        f'{school_name}: Control no. {control_number} for '
        f'{student.full_name}. Invoice {invoice.invoice_number}, '
        f'TZS {invoice.amount:,.0f}. Use when paying.'
    )
    return send_sms(parent.phone, msg[:320])


def notify_payment_received(parent, student, invoice, payment, school) -> bool:
    if not parent or not getattr(parent, 'phone', None):
        return False
    school_name = school.name if school else 'School'
    msg = (
        f'{school_name}: Payment received for {student.full_name}. '
        f'TZS {payment.amount_paid:,.0f}. Receipt {payment.receipt_number}. '
        f'Status: {invoice.status}.'
    )
    return send_sms(parent.phone, msg[:320])


def notify_announcement(recipients, announcement, school_name: Optional[str] = None) -> int:
    school = school_name or 'Elimu'
    msg = f'{school}: {announcement.title}. {announcement.content}'
    phones = [u.phone for u in recipients if getattr(u, 'phone', None)]
    return send_bulk(phones, msg[:320])


def notify_account_created(user, school_name: Optional[str] = None) -> bool:
    if not user or not getattr(user, 'phone', None):
        return False
    school = school_name or 'your school'
    msg = (
        f'Elimu: Account created for {school}. '
        f'Username: {user.username}. Role: {user.role}. '
        f'Log in with the password from your admin.'
    )
    return send_sms(user.phone, msg[:320])
