"""
Notification senders.

SECURITY: this module never hard-codes, logs, or prints any secret. All
credentials come from environment variables, which in the GitHub Actions
workflow are populated from GitHub Encrypted Secrets. If a secret is missing,
that channel is silently skipped (logged as "not configured"), never as an
error, so the monitor keeps working even if you only set up one channel.
"""
from __future__ import annotations

import os
import smtplib
from email.mime.text import MIMEText
from typing import Optional

import requests


def send_telegram(message: str, dry_run: bool = False) -> bool:
    """
    Send a message via Telegram Bot API. Returns True if sent (or dry_run),
    False if not configured or the send failed.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    if not token or not chat_id:
        print("[notify] Telegram not configured (missing secret) - skipping.")
        return False

    if dry_run:
        print("[notify] (dry-run) Would send Telegram message:")
        print(message)
        return True

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = requests.post(
            url,
            data={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        if resp.status_code != 200:
            print(f"[notify] Telegram send failed: HTTP {resp.status_code} {resp.text[:200]}")
            return False
        return True
    except requests.RequestException as exc:
        print(f"[notify] Telegram send raised an exception: {exc}")
        return False


def send_email(subject: str, message: str, dry_run: bool = False) -> bool:
    """
    Send an email via SMTP using env vars:
      SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, EMAIL_TO
    Works with any SMTP provider (Gmail app password, Outlook, Brevo free
    tier, etc). Skipped silently if not fully configured.
    """
    host = os.environ.get("SMTP_HOST", "").strip()
    port = os.environ.get("SMTP_PORT", "").strip()
    user = os.environ.get("SMTP_USER", "").strip()
    password = os.environ.get("SMTP_PASS", "").strip()
    to_addr = os.environ.get("EMAIL_TO", "").strip()

    if not all([host, port, user, password, to_addr]):
        print("[notify] Email not configured (missing one or more secrets) - skipping.")
        return False

    if dry_run:
        print(f"[notify] (dry-run) Would send email '{subject}' to {to_addr}")
        print(message)
        return True

    try:
        msg = MIMEText(message, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = user
        msg["To"] = to_addr

        with smtplib.SMTP(host, int(port), timeout=20) as server:
            server.starttls()
            server.login(user, password)
            server.sendmail(user, [to_addr], msg.as_string())
        return True
    except Exception as exc:  # noqa: BLE001 - we want to survive any SMTP failure
        print(f"[notify] Email send failed: {exc}")
        return False


def notify_all(subject: str, message: str, dry_run: bool = False) -> None:
    """Fan out a notification to every configured channel."""
    send_telegram(message, dry_run=dry_run)
    send_email(subject, message, dry_run=dry_run)
