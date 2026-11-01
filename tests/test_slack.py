from zoneinfo import ZoneInfo

import pytest
import slack_bolt
from slack_bolt.authorization import AuthorizeResult
from slack_bolt.request import BoltRequest

import app.slack_app as slack_app
from app.config import Settings
from app.db import DB
from app.service import SalesService
from tests.test_service import Clock, FakeAI, FakeMailer, lead


@pytest.fixture
def bolt(monkeypatch, tmp_path):
    # Authorize locally so no request reaches Slack's API (auth.test) during the test.
    def offline_app(token):
        result = AuthorizeResult(enterprise_id=None, team_id="T1", bot_token=token, bot_user_id="UB", bot_id="B1")
        return slack_bolt.App(authorize=lambda **kw: result)

    monkeypatch.setattr(slack_app, "App", offline_app)
    settings = Settings(db_path=str(tmp_path / "s.db"), timezone=ZoneInfo("Asia/Seoul"), slack_bot_token="xoxb-test")
    ai = FakeAI()
    ai.leads = [lead()]
    service = SalesService(DB(settings.db_path), ai, FakeMailer(), settings, clock=Clock())
    service.add_leads("x", "C1")
    service.research_and_draft(1)
    app, jobs = slack_app.create_app(service, settings)
    return app, jobs, service


def command(app, name, text):
    body = {
        "command": name, "text": text, "channel_id": "C1", "user_id": "U1", "team_id": "T1",
        "response_url": "https://hooks.slack.invalid/x", "trigger_id": "t",
    }
    return app.dispatch(BoltRequest(body=body, mode="socket_mode"))


def test_sales_commands_ack_with_text(bolt):
    app, jobs, service = bolt
    assert sorted(jobs) == ["daily_report", "followups", "replies"]

    response = command(app, "/sales", "")
    assert response.status == 200 and "오늘 (2026-10-28)" in response.body

    response = command(app, "/sales", "파이프라인")
    assert "초안 작성 — 1명" in response.body
    assert "초안 작성 — 1명" in command(app, "/sales", "pipeline").body  # English still works

    response = command(app, "/sales", "단계 Jane Kim 성사")
    assert "성사" in response.body and service.find_lead("#1")["stage"] == "won"

    assert "999번 할 일이 없어요" in command(app, "/sales", "완료 999").body
    assert "할 일 번호를 적어 주세요" in command(app, "/sales", "완료 abc").body
    assert "타깃 추가" in command(app, "/lead", "").body
    assert "통화 기록" in command(app, "/call", "").body
    assert "/sales 내일" in command(app, "/sales", "도움말").body
