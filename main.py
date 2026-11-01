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
        raise SystemExit("SLACK_BOT_TOKEN과 SLACK_APP_TOKEN을 설정하세요 (DEPLOY.md 참고).")

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
        log.warning("IMAP_HOST 미설정: 답장을 자동으로 감지하지 않아요 (/sales 단계 <리드> 대화중 으로 직접 바꾸세요).")
    if not mailer.can_send:
        log.warning("SMTP 미설정: 초안은 만들어지지만 SMTP_* 를 넣기 전에는 발송이 안 돼요.")
    if not settings.slack_channel_id:
        log.warning("SLACK_CHANNEL_ID 미설정: 매일 리포트를 보낼 채널이 없어요.")
    scheduler.start()

    public = settings.dashboard_host not in ("127.0.0.1", "localhost")
    if public and not settings.dashboard_token:
        log.warning("대시보드를 켜지 않았어요: 비밀번호 없이 공개되기 때문이에요. DASHBOARD_TOKEN을 설정하세요.")
    else:
        serve(service, settings.dashboard_host, settings.dashboard_port, settings.dashboard_token)
        log.info("대시보드: http://%s:%s", settings.dashboard_host, settings.dashboard_port)

    SocketModeHandler(app, settings.slack_app_token).start()


if __name__ == "__main__":
    main()
