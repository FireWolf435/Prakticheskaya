from __future__ import annotations

import email.message
import json
import logging
import os
import smtplib
import time
from collections.abc import Callable
from typing import Any

import psycopg

LOGGER = logging.getLogger("servicedesk.notifier")


def _settings() -> dict[str, Any]:
    dsn = os.environ.get("DATABASE_URL")
    host = os.environ.get("SMTP_HOST")
    if not dsn:
        raise RuntimeError("DATABASE_URL is not configured")
    if not host:
        raise RuntimeError("SMTP_HOST is not configured")
    return {
        "dsn": dsn,
        "host": host,
        "port": int(os.environ.get("SMTP_PORT", "25")),
        "user": os.environ.get("SMTP_USER"),
        "password": os.environ.get("SMTP_PASSWORD"),
        "sender": os.environ.get("SMTP_FROM", "support@corp.local"),
        "starttls": os.environ.get("SMTP_STARTTLS", "0") == "1",
    }


def _smtp_factory(settings: dict[str, Any]) -> smtplib.SMTP:
    client = smtplib.SMTP(settings["host"], settings["port"], timeout=15)
    if settings["starttls"]:
        client.starttls()
    if settings["user"]:
        client.login(settings["user"], settings["password"] or "")
    return client


def _message(row: tuple[Any, ...], sender: str) -> tuple[str, str, email.message.EmailMessage]:
    outbox_id, payload = row[0], row[1]
    fallback_recipient = row[2] if len(row) > 2 else None
    if isinstance(payload, str):
        payload = json.loads(payload)
    recipient = payload.get("recipient") or payload.get("to") or fallback_recipient
    if not recipient:
        raise ValueError(f"outbox {outbox_id} has no recipient")
    subject = str(payload.get("subject") or f"Service Desk: {payload.get('event_type', 'notification')}")
    body = str(payload.get("body") or payload.get("message") or "Изменение заявки в Service Desk.")
    message = email.message.EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    return str(outbox_id), str(recipient), message


def process_outbox_once(conn: Any, smtp_factory: Callable[[], Any]) -> int:
    """Send a batch of outbox events exactly once per successful claim.

    The row is claimed in the same transaction as its successful completion.
    Failed sends remain unprocessed and receive an error for diagnostics.
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT o.outbox_id, o.payload,
               COALESCE(
                   (SELECT executor.email FROM core.employees executor
                    WHERE executor.employee_id = t.executor_id),
                   author.email
               ) AS recipient
        FROM core.outbox o
        JOIN core.tickets t ON t.ticket_id = o.aggregate_id
        JOIN core.employees author ON author.employee_id = t.author_id
        WHERE o.processed_at IS NULL
        ORDER BY o.created_at, o.outbox_id
        FOR UPDATE SKIP LOCKED
        LIMIT 20
        """
    )
    rows = cursor.fetchall()
    sent = 0
    settings = _settings()
    for row in rows:
        outbox_id = row[0]
        try:
            _, recipient, message = _message(row, settings["sender"])
            smtp = smtp_factory()
            try:
                smtp.sendmail(settings["sender"], recipient, message.as_string())
            finally:
                smtp.quit()
            cursor.execute(
                "UPDATE core.outbox SET processed_at = now(), attempts = attempts + 1, last_error = NULL WHERE outbox_id = %s",
                (outbox_id,),
            )
            sent += 1
        except Exception as exc:  # keep the event for retry and diagnostics
            LOGGER.exception("Notification %s failed", outbox_id)
            cursor.execute(
                "UPDATE core.outbox SET attempts = attempts + 1, last_error = %s WHERE outbox_id = %s",
                (str(exc)[:2000], outbox_id),
            )
    conn.commit()
    return sent


def main() -> None:
    settings = _settings()
    poll_seconds = max(1, int(os.environ.get("NOTIFIER_POLL_SECONDS", "5")))
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    while True:
        try:
            with psycopg.connect(settings["dsn"]) as conn:
                process_outbox_once(conn, lambda: _smtp_factory(settings))
        except Exception:
            LOGGER.exception("Notifier iteration failed")
        time.sleep(poll_seconds)


if __name__ == "__main__":
    main()
