import smtplib
from email.message import EmailMessage
from helpers.config import get_settings

def send_email(to: str, subject: str, html: str, text_alt: str = "Please open this email in an HTML-capable client."):
    s = get_settings()
    if not s.SMTP_HOST or not s.SMTP_USER or not s.SMTP_PASS:
        # Dev fallback: print the email to logs
        print(f"[MAIL-DEV] TO={to}\nSUBJECT={subject}\n{text_alt}\nHTML:\n{html}")
        return

    msg = EmailMessage()
    msg["From"] = s.EMAIL_FROM
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text_alt)
    msg.add_alternative(html, subtype="html")

    with smtplib.SMTP_SSL(s.SMTP_HOST, s.SMTP_PORT) as client:
        client.login(s.SMTP_USER, s.SMTP_PASS)
        client.send_message(msg)
