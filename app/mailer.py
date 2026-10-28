"""Sending email (SMTP), spotting replies (IMAP) and building calendar invites (.ics)."""

from __future__ import annotations

import email
import imaplib
import smtplib
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

from .config import Settings


class MailError(RuntimeError):
    pass


class Mailer:
    def __init__(self, settings: Settings):
        self.s = settings

    @property
    def can_send(self) -> bool:
        return bool(self.s.smtp_host and self.s.smtp_user and self.s.smtp_password and self.s.sender_email)

    @property
    def can_read(self) -> bool:
        return bool(self.s.imap_host and self.s.smtp_user and self.s.smtp_password)

    def build_message(self, to: str, subject: str, body: str, in_reply_to: str = "", ics: str = "") -> EmailMessage:
        msg = EmailMessage()
        msg["From"] = formataddr((self.s.sender_name, self.s.sender_email))
        msg["To"] = to
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain=self.s.sender_email.split("@")[-1] or None)
        msg["List-Unsubscribe"] = f"<mailto:{self.s.sender_email}?subject=unsubscribe>"
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
            msg["References"] = in_reply_to
        msg.set_content(body)
        if ics:
            msg.add_attachment(ics, subtype="calendar", filename="invite.ics", params={"method": "REQUEST"})
        return msg

    def send(self, to: str, subject: str, body: str, in_reply_to: str = "", ics: str = "") -> str:
        """Send one email and return its Message-ID (used to thread follow-ups)."""
        if not self.can_send:
            raise MailError("SMTP is not configured (SMTP_HOST, SMTP_USER, SMTP_PASSWORD, SENDER_EMAIL).")
        msg = self.build_message(to, subject, body, in_reply_to, ics)
        try:
            if self.s.smtp_port == 465:
                server = smtplib.SMTP_SSL(self.s.smtp_host, self.s.smtp_port, timeout=30)
            else:
                server = smtplib.SMTP(self.s.smtp_host, self.s.smtp_port, timeout=30)
                server.starttls()
            with server:
                server.login(self.s.smtp_user, self.s.smtp_password)
                server.send_message(msg)
        except (smtplib.SMTPException, OSError) as exc:
            raise MailError(f"Sending failed: {exc}") from exc
        return msg["Message-ID"]

    def find_replies(self, since_by_address: dict[str, datetime]) -> dict[str, list[dict[str, str]]]:
        """Return inbox messages from each address received on/after its date: {address: [{subject, snippet}]}."""
        if not self.can_read or not since_by_address:
            return {}
        found: dict[str, list[dict[str, str]]] = {}
        try:
            with imaplib.IMAP4_SSL(self.s.imap_host) as imap:
                imap.login(self.s.smtp_user, self.s.smtp_password)
                imap.select("INBOX", readonly=True)
                for address, since in since_by_address.items():
                    status, data = imap.search(None, "FROM", f'"{address}"', "SINCE", since.strftime("%d-%b-%Y"))
                    if status != "OK" or not data or not data[0]:
                        continue
                    for num in data[0].split()[-3:]:
                        status, parts = imap.fetch(num, "(BODY.PEEK[])")
                        if status != "OK" or not parts or not isinstance(parts[0], tuple):
                            continue
                        found.setdefault(address, []).append(_summarize(email.message_from_bytes(parts[0][1])))
        except (imaplib.IMAP4.error, OSError) as exc:
            raise MailError(f"Checking the inbox failed: {exc}") from exc
        return found


def _summarize(msg: email.message.Message) -> dict[str, str]:
    subject = str(make_header(decode_header(msg.get("Subject", ""))))
    text = ""
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.get_content_type() == "text/plain":
            payload = part.get_payload(decode=True) or b""
            text = payload.decode(part.get_content_charset() or "utf-8", "replace")
            break
    # Drop the quoted original so the snippet shows what they actually wrote.
    lines = [line for line in text.splitlines() if not line.startswith(">")]
    snippet = " ".join(" ".join(lines).split())[:400]
    return {"subject": subject, "snippet": snippet}


def _ics_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def build_ics(
    *,
    uid: str,
    start: datetime,
    minutes: int,
    summary: str,
    description: str,
    location: str,
    organizer_name: str,
    organizer_email: str,
    attendee_name: str,
    attendee_email: str,
    now: datetime,
) -> str:
    fmt = "%Y%m%dT%H%M%SZ"
    start_utc = start.astimezone(timezone.utc)
    lines = [
        "BEGIN:VCALENDAR",
        "PRODID:-//Sales Agent//EN",
        "VERSION:2.0",
        "CALSCALE:GREGORIAN",
        "METHOD:REQUEST",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{now.astimezone(timezone.utc).strftime(fmt)}",
        f"DTSTART:{start_utc.strftime(fmt)}",
        f"DTEND:{(start_utc + timedelta(minutes=minutes)).strftime(fmt)}",
        f"SUMMARY:{_ics_escape(summary)}",
        f"DESCRIPTION:{_ics_escape(description)}",
        f"LOCATION:{_ics_escape(location)}",
        f"ORGANIZER;CN={_ics_escape(organizer_name)}:mailto:{organizer_email}",
        f"ATTENDEE;CN={_ics_escape(attendee_name)};ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:{attendee_email}",
        "STATUS:CONFIRMED",
        "SEQUENCE:0",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "\r\n".join(lines) + "\r\n"
