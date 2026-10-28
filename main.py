"""Start the sales agent: Slack bot (Socket Mode) + scheduler + dashboard."""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from slack_bolt.adapter.socket_mode import SocketModeHandler

from app.ai import SalesAI
from app.config import Settings
from app.dashboard import serve
from app.db import DB
from app.mailer import Mailer
from app.service import SalesService
from app.slack_app import create_app

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("sales-agent")
    settings = Settings.from_env()
    if not (settings.slack_bot_token and settings.slack_app_token):
        raise SystemExit("Set SLACK_BOT_TOKEN and SLACK_APP_TOKEN (see README.md).")

    mailer = Mailer(settings)
    service = SalesService(DB(settings.db_path), SalesAI(settings), mailer, settings)
    app, jobs = create_app(service, settings)

    scheduler = BackgroundScheduler(timezone=settings.timezone)
    fu_hour, fu_minute = (int(x) for x in settings.followup_time.split(":"))
    report_hour, report_minute = (int(x) for x in settings.daily_report_time.split(":"))
    scheduler.add_job(jobs["followups"], "cron", day_of_week=settings.workdays, hour=fu_hour, minute=fu_minute)
    scheduler.add_job(jobs["daily_report"], "cron", day_of_week=settings.workdays, hour=report_hour, minute=report_minute)
    if mailer.can_read:
        scheduler.add_job(jobs["replies"], "interval", minutes=15)
    else:
        log.warning("IMAP_HOST not set: replies won't be detected automatically (use /sales stage <lead> engaged).")
    if not mailer.can_send:
        log.warning("SMTP not configured: drafts work, but Send will fail until SMTP_* is set.")
    if not settings.slack_channel_id:
        log.warning("SLACK_CHANNEL_ID not set: the daily report has nowhere to go.")
    scheduler.start()

    serve(service, settings.dashboard_host, settings.dashboard_port, settings.dashboard_token)
    log.info("Dashboard on http://%s:%s", settings.dashboard_host, settings.dashboard_port)

    SocketModeHandler(app, settings.slack_app_token).start()


if __name__ == "__main__":
    main()
