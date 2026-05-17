from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path

from dotenv import load_dotenv

LOGGER = logging.getLogger(__name__)


def send_brief_email(
    *,
    subject: str,
    markdown_path: str | Path,
    html_path: str | Path,
) -> bool:
    load_dotenv()
    config = email_config()
    if not config:
        print(f"Email not configured. Brief generated at {markdown_path} and {html_path}.")
        return False

    markdown_text = Path(markdown_path).read_text(encoding="utf-8")
    html_text = Path(html_path).read_text(encoding="utf-8")
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config["from"]
    message["To"] = ", ".join(config["to"])
    message.set_content(markdown_text)
    message.add_alternative(html_text, subtype="html")

    if config["port"] == 465:
        with smtplib.SMTP_SSL(config["host"], config["port"], timeout=30) as smtp:
            login_if_configured(smtp, config)
            smtp.send_message(message)
    else:
        with smtplib.SMTP(config["host"], config["port"], timeout=30) as smtp:
            smtp.starttls()
            login_if_configured(smtp, config)
            smtp.send_message(message)

    LOGGER.info("Sent daily brief email to %s", ", ".join(config["to"]))
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


def login_if_configured(smtp: smtplib.SMTP, config: dict[str, object]) -> None:
    user = config.get("user")
    password = config.get("password")
    if user and password:
        smtp.login(str(user), str(password))
