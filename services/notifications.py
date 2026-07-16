"""
Notification facade — email (Gmail) + SMS (Africa's Talking).

Controllers call this module so channel details stay in services/.
"""

from __future__ import annotations

from typing import Optional

from services import email_service, sms_service


def channels_status() -> dict:
    """Return which notification channels are configured."""
    return {
        'email': email_service.is_configured(),
        'sms': sms_service.is_configured(),
    }


def notify_account_created(user, school_name: Optional[str] = None) -> dict:
    """Welcome notice on account create (email + SMS)."""
    return {
        'email': email_service.notify_account_created(user, school_name),
        'sms': sms_service.notify_account_created(user, school_name),
    }


def notify_invoice_created(parent, student, invoice, school) -> dict:
    return {
        'email': email_service.notify_invoice_created(parent, student, invoice, school),
        'sms': sms_service.notify_invoice_created(parent, student, invoice, school),
    }


def notify_control_number(parent, student, invoice, control_number, school) -> dict:
    return {
        'email': email_service.notify_control_number(
            parent, student, invoice, control_number, school
        ),
        'sms': sms_service.notify_control_number(
            parent, student, invoice, control_number, school
        ),
    }


def notify_payment_received(parent, student, invoice, payment, school) -> dict:
    return {
        'email': email_service.notify_payment_received(
            parent, student, invoice, payment, school
        ),
        'sms': sms_service.notify_payment_received(
            parent, student, invoice, payment, school
        ),
    }


def notify_announcement(recipients, announcement, school_name: Optional[str] = None) -> dict:
    return {
        'email': email_service.notify_announcement(recipients, announcement, school_name),
        'sms': sms_service.notify_announcement(recipients, announcement, school_name),
    }


def notify_password_reset_otp(
    user,
    otp: str,
    expires_minutes: int = 10,
    school_name: Optional[str] = None,
) -> dict:
    """Email OTP for parent password reset (SMS not used)."""
    return {
        'email': email_service.notify_password_reset_otp(
            user, otp, expires_minutes, school_name
        ),
        'sms': False,
    }


def summarize_channels(result: dict) -> str:
    """Human-readable summary for flash messages."""
    parts = []
    if result.get('email'):
        parts.append('email')
    if result.get('sms'):
        parts.append('SMS')
    if not parts:
        return ''
    return ' + '.join(parts)
