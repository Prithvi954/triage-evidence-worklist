"""Outgoing email (activation codes).

Configure SMTP with environment variables (never in code). Works with Gmail (app password),
SendGrid, Resend, Brevo, Mailgun, Amazon SES and most other providers:
  TRIAGE_SMTP_HOST, TRIAGE_SMTP_PORT (587), TRIAGE_SMTP_USER, TRIAGE_SMTP_PASSWORD, TRIAGE_SMTP_FROM

Without SMTP the code is printed to the server console (for local testing only), with the
address partly hidden. Clinical deployments refuse to start without SMTP.
"""
import smtplib
import ssl
import sys
from email.message import EmailMessage

sent = []   # tests read this: (to, subject, body)


def mask(addr):
    name, _, domain = addr.partition("@")
    return (name[:1] + "***@" + domain) if domain else "***"


def configured(s):
    return bool(s.smtp_host and s.smtp_from)


def send_code(s, to, name, code, minutes):
    subject = f"Your activation code: {code}"
    body = (f"Hi {name},\n\n"
            f"Your Triage Practice activation code is:\n\n    {code}\n\n"
            f"It expires in {minutes} minutes. Enter it on the sign-up page to activate your account.\n\n"
            "If you didn't sign up, you can ignore this email.\n\n"
            "Triage Practice is a training tool with made-up patients. Never enter real patient information.\n")
    sent.append((to, subject, body))
    if not configured(s):
        print(f"[email not configured] activation code for {mask(to)}: {code}", file=sys.stderr, flush=True)
        return "console"
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = s.smtp_from, to, subject
    msg.set_content(body)
    port = int(s.smtp_port or 587)
    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(s.smtp_host, port, context=ctx, timeout=20) as m:
            if s.smtp_user:
                m.login(s.smtp_user, s.smtp_password)
            m.send_message(msg)
    else:
        with smtplib.SMTP(s.smtp_host, port, timeout=20) as m:
            m.starttls(context=ctx)
            if s.smtp_user:
                m.login(s.smtp_user, s.smtp_password)
            m.send_message(msg)
    return "smtp"
