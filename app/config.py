"""Settings loaded from environment variables (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _int_list(value: str) -> list[int]:
    return [int(part) for part in value.replace(" ", "").split(",") if part]


@dataclass
class Settings:
    # Storage / runtime
    db_path: str = "sales.db"
    timezone: ZoneInfo = ZoneInfo("Asia/Seoul")

    # Claude
    model: str = "claude-opus-5-5"
    research_mode: str = "web"  # "web" = Claude web search, "site" = company homepage only
    email_language: str = "Korean"

    # Who is sending, and what we sell
    sender_name: str = ""
    sender_title: str = ""
    sender_company: str = ""
    sender_email: str = ""
    value_prop: str = ""
    booking_link: str = ""
    meeting_link: str = ""
    opt_out_line: str = "관련 없으시면 \"괜찮습니다\"라고만 답장 주세요. 더 연락드리지 않겠습니다."

    # Email (SMTP to send, IMAP to detect replies)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    imap_host: str = ""
    send_calendar_invites: bool = True
    daily_send_limit: int = 40

    # Cadence
    followup_intervals: tuple[int, ...] = (3, 4)  # days after intro, then after follow-up #1
    call_after_days: int = 2  # call task this many days after the last email
    followup_time: str = "09:00"
    daily_report_time: str = "18:00"
    workdays: str = "mon-fri"

    # Slack
    slack_bot_token: str = ""
    slack_app_token: str = ""
    slack_channel_id: str = ""  # default channel for reports, reply alerts and follow-up drafts

    # Dashboard
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8080
    dashboard_token: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            db_path=_env("DB_PATH", "sales.db"),
            timezone=ZoneInfo(_env("TIMEZONE", "Asia/Seoul")),
            model=_env("CLAUDE_MODEL", "claude-opus-5-5"),
            research_mode=_env("RESEARCH_MODE", "web").lower(),
            email_language=_env("EMAIL_LANGUAGE", "Korean"),
            sender_name=_env("SENDER_NAME"),
            sender_title=_env("SENDER_TITLE"),
            sender_company=_env("SENDER_COMPANY"),
            sender_email=_env("SENDER_EMAIL") or _env("SMTP_USER"),
            value_prop=_env("VALUE_PROP"),
            booking_link=_env("BOOKING_LINK"),
            meeting_link=_env("MEETING_LINK"),
            opt_out_line=_env("OPT_OUT_LINE", cls.opt_out_line),
            smtp_host=_env("SMTP_HOST"),
            smtp_port=int(_env("SMTP_PORT", "587")),
            smtp_user=_env("SMTP_USER"),
            smtp_password=_env("SMTP_PASSWORD"),
            imap_host=_env("IMAP_HOST"),
            send_calendar_invites=_env("SEND_CALENDAR_INVITES", "true").lower() != "false",
            daily_send_limit=int(_env("DAILY_SEND_LIMIT", "40")),
            followup_intervals=tuple(_int_list(_env("FOLLOWUP_INTERVALS", "3,4"))),
            call_after_days=int(_env("CALL_AFTER_DAYS", "2")),
            followup_time=_env("FOLLOWUP_TIME", "09:00"),
            daily_report_time=_env("DAILY_REPORT_TIME", "18:00"),
            workdays=_env("WORKDAYS", "mon-fri"),
            slack_bot_token=_env("SLACK_BOT_TOKEN"),
            slack_app_token=_env("SLACK_APP_TOKEN"),
            slack_channel_id=_env("SLACK_CHANNEL_ID"),
            # Hosting platforms (Railway, Render, Fly) hand us PORT and expect us to listen publicly.
            dashboard_host=_env("DASHBOARD_HOST", "0.0.0.0" if _env("PORT") else "127.0.0.1"),
            dashboard_port=int(_env("DASHBOARD_PORT") or _env("PORT") or "8080"),
            dashboard_token=_env("DASHBOARD_TOKEN"),
        )

    @property
    def signature(self) -> str:
        role = ", ".join(part for part in (self.sender_title, self.sender_company) if part)
        return "\n".join(part for part in (self.sender_name, role) if part)
