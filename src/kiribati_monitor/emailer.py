from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

from dotenv import load_dotenv

LOGGER = logging.getLogger(__name__)
REQUIRED_EMAIL_ENV = ("SMTP_HOST", "EMAIL_FROM", "EMAIL_TO")


def send_brief_email(
    *,
    subject: str,
    markdown_path: str | Path,
    html_path: str | Path,
) -> bool:
    load_dotenv()
    config = email_config()
    if not config:
        missing = missing_email_config()
        LOGGER.warning(
            "Email not configured; missing required environment variables: %s",
            ", ".join(missing) if missing else "invalid EMAIL_TO",
        )
        print(f"Email not configured. Brief generated at {markdown_path} and {html_path}.")
        return False

    markdown_text = Path(markdown_path).read_text(encoding="utf-8")
    html_text = Path(html_path).read_text(encoding="utf-8")
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config["from"]
    message["To"] = ", ".join(config["to"])
    message["Date"] = formatdate(localtime=False, usegmt=True)
    message["Message-ID"] = make_msgid(domain="kiribati-macro-monitor.local")
    message.set_content(markdown_text)
    message.add_alternative(html_text, subtype="html")
    LOGGER.info(
        "Email configuration detected: smtp_host=%s smtp_port=%s recipients=%s masked_to=%s smtp_auth=%s",
        config["host"],
        config["port"],
        len(config["to"]),
        ", ".join(mask_email(str(email)) for email in config["to"]),
        "yes" if config.get("user") and config.get("password") else "no",
    )

    if config["port"] == 465:
        with smtplib.SMTP_SSL(config["host"], config["port"], timeout=30) as smtp:
            login_if_configured(smtp, config)
            refused = smtp.send_message(message)
    else:
        with smtplib.SMTP(config["host"], config["port"], timeout=30) as smtp:
            smtp.starttls()
            login_if_configured(smtp, config)
            refused = smtp.send_message(message)

    if refused:
        refused_recipients = ", ".join(mask_email(str(email)) for email in refused)
        raise RuntimeError(f"SMTP refused recipients: {refused_recipients}")

    LOGGER.info(
        "SMTP accepted daily brief email: message_id=%s masked_to=%s",
        message["Message-ID"],
        ", ".join(mask_email(str(email)) for email in config["to"]),
    )
    return True


def email_config() -> dict[str, object] | None:
    required = {
        "host": os.getenv("SMTP_HOST"),
        "from": os.getenv("EMAIL_FROM"),
        "to": os.getenv("EMAIL_TO"),
    }
    if not all(required.values()):
        return None
    recipients = [email.strip() for email in str(required["to"]).split(",") if email.strip()]
    if not recipients:
        return None
    port = int(os.getenv("SMTP_PORT") or "587")
    return {
        "host": str(required["host"]),
        "port": port,
        "user": os.getenv("SMTP_USER"),
        "password": os.getenv("SMTP_PASSWORD"),
        "from": str(required["from"]),
        "to": recipients,
    }


def missing_email_config() -> list[str]:
    return [name for name in REQUIRED_EMAIL_ENV if not os.getenv(name)]


def login_if_configured(smtp: smtplib.SMTP, config: dict[str, object]) -> None:
    user = config.get("user")
    password = config.get("password")
    if user and password:
        smtp.login(str(user), str(password))


def mask_email(email: str) -> str:
    if "@" not in email:
        return "***"
    local, domain = email.split("@", 1)
    if not local:
        return f"***@{domain}"
    return f"{local[0]}***@{domain}"
