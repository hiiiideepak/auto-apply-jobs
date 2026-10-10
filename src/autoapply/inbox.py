"""Read a verification code from the candidate's own inbox.

Credentials stay in .env (IMAP_USER, IMAP_PASSWORD). This only looks at recent
messages that mention an application verification code.
"""

from __future__ import annotations

import email
import imaplib
import os
import re
import time
from datetime import datetime, timedelta, timezone
from email.header import decode_header
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_ENV_PATH = ROOT / ".env"

_LABELED_CODE = re.compile(
    r"(?:verification|security|one[- ]time)\s+code(?:\s+(?:is|was))?\s*[:\-]?\s*([A-Za-z0-9]{6,8})",
    re.I,
)
_CODE_WORD = re.compile(
    r"\bcode(?:\s+(?:is|was))?\s*[:\-]?\s*([A-Za-z0-9]{8})\b",
    re.I,
)
_EIGHT_DIGITS = re.compile(r"(?<!\d)(\d{8})(?!\d)")
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


class InboxError(Exception):
    pass


def load_dotenv() -> None:
    if not _ENV_PATH.is_file():
        return
    for line in _ENV_PATH.read_text().splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        # A later non-empty value wins. The template leaves IMAP_PASSWORD blank
        # and the real app password is appended below it.
        if value and not os.environ.get(key):
            os.environ[key] = value
        elif key not in os.environ:
            os.environ[key] = value


def inbox_configured() -> bool:
    load_dotenv()
    user = os.environ.get("IMAP_USER") or os.environ.get("SMTP_USER")
    password = os.environ.get("IMAP_PASSWORD") or os.environ.get("SMTP_PASSWORD")
    return bool(user and password)


def extract_verification_code(text: str) -> str | None:
    plain = _SPACE.sub(" ", _TAG.sub(" ", text or ""))
    labeled = _LABELED_CODE.search(plain)
    if labeled:
        return labeled.group(1)
    if not re.search(r"verif|security code", plain, re.I):
        return None
    coded = _CODE_WORD.search(plain)
    if coded:
        return coded.group(1)
    digits = _EIGHT_DIGITS.search(plain)
    if digits:
        return digits.group(1)
    return None


def wait_for_verification_code(not_before: datetime, timeout: float = 75) -> str | None:
    """Poll the inbox until a code newer than not_before arrives."""
    if not inbox_configured():
        return None
    deadline = time.time() + timeout
    while True:
        code = _scan_inbox(not_before)
        if code:
            return code
        if time.time() >= deadline:
            return None
        time.sleep(5)


def _credentials() -> tuple[str, str, str]:
    load_dotenv()
    host = os.environ.get("IMAP_HOST") or "imap.gmail.com"
    user = os.environ.get("IMAP_USER") or os.environ.get("SMTP_USER") or ""
    password = (os.environ.get("IMAP_PASSWORD") or os.environ.get("SMTP_PASSWORD") or "").replace(" ", "")
    if not user or not password:
        raise InboxError("IMAP_USER and IMAP_PASSWORD are not set.")
    return host, user, password


def _scan_inbox(not_before: datetime) -> str | None:
    host, user, password = _credentials()
    try:
        mailbox = imaplib.IMAP4_SSL(host)
        mailbox.login(user, password)
    except imaplib.IMAP4.error as exc:
        raise InboxError(
            "Could not sign in to the inbox. Use a Gmail app password in IMAP_PASSWORD."
        ) from exc
    try:
        mailbox.select("INBOX", readonly=True)
        since = (not_before - timedelta(days=1)).strftime("%d-%b-%Y")
        status, data = mailbox.search(None, f"(SINCE {since})")
        if status != "OK" or not data or not data[0]:
            return None
        for message_id in reversed(data[0].split()[-40:]):
            status, fetched = mailbox.fetch(message_id, "(INTERNALDATE BODY.PEEK[])")
            if status != "OK":
                continue
            when, message = _parse_fetch(fetched)
            if when is None or when < not_before.astimezone(timezone.utc):
                continue
            subject = _header(message, "Subject")
            body = _body(message)
            blob = f"{subject}\n{body}"
            if not re.search(r"verif|security code", blob, re.I):
                continue
            code = extract_verification_code(blob)
            if code:
                return code
        return None
    finally:
        try:
            mailbox.logout()
        except imaplib.IMAP4.error:
            pass


def _parse_fetch(fetched) -> tuple[datetime | None, email.message.Message | None]:
    if not fetched:
        return None, None
    for item in fetched:
        if not isinstance(item, tuple) or len(item) < 2:
            continue
        meta, raw = item[0], item[1]
        if not isinstance(raw, (bytes, bytearray)):
            continue
        when = None
        if isinstance(meta, (bytes, bytearray)):
            match = re.search(br'INTERNALDATE "([^"]+)"', meta)
            if match:
                when = parsedate_to_datetime(match.group(1).decode())
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                when = when.astimezone(timezone.utc)
        return when, email.message_from_bytes(raw)
    return None, None


def _header(message: email.message.Message, name: str) -> str:
    parts = []
    for value, charset in decode_header(message.get(name) or ""):
        if isinstance(value, bytes):
            parts.append(value.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(value)
    return "".join(parts)


def _body(message: email.message.Message) -> str:
    chunks: list[str] = []
    parts = message.walk() if message.is_multipart() else [message]
    for part in parts:
        if part.get_content_type() not in {"text/plain", "text/html"}:
            continue
        payload = part.get_payload(decode=True) or b""
        charset = part.get_content_charset() or "utf-8"
        chunks.append(payload.decode(charset, errors="replace"))
    return "\n".join(chunks)
