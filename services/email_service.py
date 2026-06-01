import imaplib
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Tuple
from models.email_config import EmailConfig


def test_imap_connection(config: EmailConfig) -> Tuple[bool, str]:
    """Test IMAP connectivity. Returns (success, message)."""
    try:
        if config.imap_use_ssl:
            context = ssl.create_default_context()
            mail = imaplib.IMAP4_SSL(config.imap_host, config.imap_port, ssl_context=context)
        else:
            mail = imaplib.IMAP4(config.imap_host, config.imap_port)
        mail.login(config.imap_username, config.imap_password)
        mail.select(config.imap_folder or "INBOX")
        mail.logout()
        return True, "IMAP connection successful."
    except imaplib.IMAP4.error as exc:
        return False, f"IMAP authentication error: {exc}"
    except OSError as exc:
        return False, f"IMAP connection error: {exc}"
    except Exception as exc:
        return False, f"Unexpected error: {exc}"


def test_smtp_connection(config: EmailConfig) -> Tuple[bool, str]:
    """Test SMTP connectivity. Returns (success, message)."""
    try:
        if config.smtp_use_tls:
            server = smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=10)
            server.ehlo()
            server.starttls()
            server.ehlo()
        else:
            server = smtplib.SMTP_SSL(config.smtp_host, config.smtp_port, timeout=10)
        server.login(config.smtp_username, config.smtp_password)
        server.quit()
        return True, "SMTP connection successful."
    except smtplib.SMTPAuthenticationError as exc:
        return False, f"SMTP authentication failed: {exc}"
    except OSError as exc:
        return False, f"SMTP connection error: {exc}"
    except Exception as exc:
        return False, f"Unexpected error: {exc}"


def send_email(
    config: EmailConfig,
    to_address: str,
    subject: str,
    body_html: str,
    body_text: str = ""
) -> Tuple[bool, str]:
    """Send an email using the given SMTP configuration."""
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{config.smtp_from_name} <{config.smtp_from_email}>"
        msg["To"] = to_address

        if body_text:
            msg.attach(MIMEText(body_text, "plain"))
        msg.attach(MIMEText(body_html, "html"))

        if config.smtp_use_tls:
            server = smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=10)
            server.starttls()
        else:
            server = smtplib.SMTP_SSL(config.smtp_host, config.smtp_port, timeout=10)

        server.login(config.smtp_username, config.smtp_password)
        server.sendmail(config.smtp_from_email, to_address, msg.as_string())
        server.quit()
        return True, "Email sent successfully."
    except Exception as exc:
        return False, str(exc)
