from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app import formatting as fmt
from app.ai import CallReview, FollowUpDraft, OutreachDraft, ParsedLead, SalesAI
from app.config import Settings
from app.dashboard import render
from app.db import DB
from app.service import LeadNotFound, SalesService
from app.slack_app import _chunks


class FakeAI:
    def __init__(self):
        self.leads = []
        self.review = None
        self.plans = []

    def parse_leads(self, text):
        return self.leads

    def research(self, lead):
        return f"web research about {lead['company']}"

    def draft_intro(self, lead, research):
        return OutreachDraft(
            company_summary=f"{lead['company']} sells shoes online.",
            person_insights="Founder-CEO, talks about margins.",
            personalization_hooks=["Opened 3 stores in 2026"],
            subject="your new stores",
            body=f"Hi {lead['name'].split()[0]}, congrats on the stores.",
        )

    def draft_followup(self, lead, sent, step, final):
        return FollowUpDraft(subject="x", body=f"follow-up {step}{' (final)' if final else ''}")

    def review_call(self, lead, notes, hint, history):
        return self.review or CallReview(
            outcome="connected", summary="Liked it.", sentiment="positive", went_well="Good opener",
            to_improve="Ask budget", next_step="Send pricing", next_step_kind="email", next_step_in_days=2,
        )

    def plan_day(self, context):
        self.plans.append(context)
        return "*Top 3*\n• Call Jane"


class FakeMailer:
    can_send = True
    can_read = True

    def __init__(self):
        self.sent = []
        self.replies = {}

    def send(self, to, subject, body, in_reply_to="", ics=""):
        self.sent.append({"to": to, "subject": subject, "body": body, "in_reply_to": in_reply_to, "ics": ics})
        return f"<msg{len(self.sent)}@acme.io>"

    def find_replies(self, since):
        return {addr: found for addr, found in self.replies.items() if addr in since}


class Clock:
    def __init__(self):
        # Wednesday 2026-10-28 10:00 in Seoul
        self.now = datetime(2026, 10, 28, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))

    def __call__(self):
        return self.now.astimezone(timezone.utc)

    def advance(self, **kw):
        self.now += timedelta(**kw)


def lead(name="Jane Kim", company="Acme Commerce", email="jane@acmecommerce.com", **kw):
    data = dict(name=name, title="CEO", company=company, email=email, website="acmecommerce.com", linkedin="", phone="", notes="")
    data.update(kw)
    return ParsedLead(**data)


@pytest.fixture
def env(tmp_path):
    settings = Settings(
        db_path=str(tmp_path / "t.db"), timezone=ZoneInfo("Asia/Seoul"), sender_name="Min Park",
        sender_title="Founder", sender_company="ShipFast", sender_email="min@shipfast.io",
        followup_intervals=(3, 4), call_after_days=2, daily_send_limit=5,
    )
    ai, mailer, clock = FakeAI(), FakeMailer(), Clock()
    service = SalesService(DB(settings.db_path), ai, mailer, settings, clock=clock)
    return SimpleNamespace(service=service, ai=ai, mailer=mailer, clock=clock, settings=settings)


def add_and_draft(env, *leads):
    env.ai.leads = list(leads)
    results = env.service.add_leads("whatever", "C123")
    return [env.service.research_and_draft(l["id"]) for l, created in results if created]


def test_add_leads_dedupes_by_email(env):
    env.ai.leads = [lead()]
    first = env.service.add_leads("x")
    second = env.service.add_leads("x")
    assert first[0][1] is True and second[0][1] is False
    assert env.service.db.scalar("SELECT count(*) FROM leads") == 1


def test_draft_appends_signature_and_opt_out(env):
    (email,) = add_and_draft(env, lead())
    assert email["status"] == "draft" and email["step"] == 0
    assert "Min Park\nFounder, ShipFast" in email["body"]
    assert env.settings.opt_out_line in email["body"]
    stored = env.service.db.get("leads", email["lead_id"])
    assert stored["stage"] == "drafted" and "Opened 3 stores" in stored["research"]


def test_full_email_sequence(env):
    (email,) = add_and_draft(env, lead())
    svc = env.service
    sent, l = svc.send_email(email["id"])
    assert l["stage"] == "contacted" and sent["message_id"] == "<msg1@acme.io>"
    task = svc.db.one("SELECT * FROM tasks WHERE status = 'open'")
    assert task["kind"] == "followup_email" and task["due_date"] == "2026-10-31" and task["step"] == 1

    assert svc.draft_due_followups() == []  # not due yet
    env.clock.advance(days=3)
    [(l, fu1)] = svc.draft_due_followups()
    assert fu1["subject"] == "Re: your new stores" and fu1["in_reply_to"] == "<msg1@acme.io>"
    assert fu1["body"].startswith("follow-up 1")
    svc.send_email(fu1["id"])
    task = svc.db.one("SELECT * FROM tasks WHERE status = 'open'")
    assert task["step"] == 2 and task["due_date"] == "2026-11-04"

    env.clock.advance(days=4)
    [(l, fu2)] = svc.draft_due_followups()
    assert "(final)" in fu2["body"] and fu2["in_reply_to"] == "<msg2@acme.io>"
    svc.send_email(fu2["id"])
    task = svc.db.one("SELECT * FROM tasks WHERE status = 'open'")
    assert task["kind"] == "call" and task["due_date"] == "2026-11-06"
    assert len(env.mailer.sent) == 3


def test_cannot_send_twice_or_over_limit(env):
    emails = add_and_draft(env, *[lead(name=f"P{i}", company=f"Co{i}", email=f"p{i}@co{i}.com") for i in range(6)])
    env.service.send_email(emails[0]["id"])
    with pytest.raises(ValueError, match="이미 발송"):
        env.service.send_email(emails[0]["id"])
    for e in emails[1:5]:
        env.service.send_email(e["id"])
    with pytest.raises(ValueError, match="발송 한도"):
        env.service.send_email(emails[5]["id"])


def test_reply_stops_sequence(env):
    (email,) = add_and_draft(env, lead())
    env.service.send_email(email["id"])
    env.mailer.replies = {"jane@acmecommerce.com": [{"subject": "Re: your new stores", "snippet": "Sure, Thursday?"}]}
    [(l, found)] = env.service.check_replies()
    assert l["stage"] == "engaged"
    kinds = {t["kind"]: t["status"] for t in env.service.db.query("SELECT * FROM tasks")}
    assert kinds == {"followup_email": "canceled", "reply": "open"}
    assert env.service.check_replies() == []  # no longer 'contacted'


def test_log_call_matches_name_prefix_and_alias(env):
    (email,) = add_and_draft(env, lead())
    env.service.send_email(email["id"])
    l, review, task = env.service.log_call("Jane Kim vm left a message about Q4")
    call = env.service.db.one("SELECT * FROM activities WHERE type = 'call'")
    # The fake AI says "connected", but the rep's own choice wins everywhere, including the Slack message.
    assert call["outcome"] == "voicemail" and review.outcome == "voicemail" and call["notes"] == "left a message about Q4"
    assert "음성메시지" in fmt.call_text(l, review, task)
    assert l["stage"] == "contacted"  # voicemail doesn't move the stage or stop follow-ups
    assert env.service.db.scalar("SELECT count(*) FROM tasks WHERE kind = 'followup_email' AND status = 'open'") == 1
    assert task["due_date"] == "2026-10-30"


def test_connected_call_stops_followups(env):
    (email,) = add_and_draft(env, lead())
    env.service.send_email(email["id"])
    l, review, task = env.service.log_call("#1 connected wants pricing")
    assert l["stage"] == "engaged"
    assert env.service.db.scalar("SELECT count(*) FROM tasks WHERE kind = 'followup_email' AND status = 'open'") == 0
    assert task["kind"] == "email" and "Send pricing" in task["title"]


def test_not_interested_marks_lost_without_task(env):
    add_and_draft(env, lead())
    env.ai.review = CallReview(
        outcome="not_interested", summary="No budget.", sentiment="negative", went_well="-", to_improve="-",
        next_step="Check back in Q3", next_step_kind="call", next_step_in_days=90,
    )
    l, review, task = env.service.log_call("Acme Commerce | said no budget this year")
    assert l["stage"] == "lost" and task is None


def test_ambiguous_lead_suggests(env):
    add_and_draft(env, lead(name="Jane Kim"), lead(name="Jane Lee", company="Beta", email="jane@beta.com"))
    with pytest.raises(LeadNotFound) as err:
        env.service.find_lead("jane")
    assert len(err.value.suggestions) == 2
    with pytest.raises(LeadNotFound):
        env.service.split_lead_ref("Nobody here connected")


def test_book_meeting_sends_invite(env):
    (email,) = add_and_draft(env, lead())
    env.service.send_email(email["id"])
    l, meeting, invited = env.service.book_meeting("Jane Kim tomorrow 14:30 45")
    assert invited and l["stage"] == "meeting_booked"
    assert meeting["starts_at"] == "2026-10-29T05:30:00+00:00" and meeting["duration_min"] == 45
    invite = env.mailer.sent[-1]
    assert "DTSTART:20261029T053000Z" in invite["ics"] and "DTEND:20261029T061500Z" in invite["ics"]
    assert "ATTENDEE;CN=Jane Kim" in invite["ics"]
    assert env.service.db.scalar("SELECT count(*) FROM tasks WHERE status = 'open'") == 0

    agenda = env.service.agenda(env.service.today() + timedelta(days=1))
    assert [m["local_time"] for m in agenda["meetings"]] == ["14:30"]


def test_bad_meeting_time(env):
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        env.service.book_meeting("Jane next week")


def test_korean_commands(env):
    (email,) = add_and_draft(env, lead(name="김지현", company="루미에르몰", email="jihyun@lumiere.kr", title="대표"))
    env.service.send_email(email["id"])
    l, review, task = env.service.log_call("김지현 부재 오후에 다시")
    assert review.outcome == "no_answer"
    l, meeting, invited = env.service.book_meeting("김지현 내일 15:00 45분")
    assert meeting["starts_at"] == "2026-10-29T06:00:00+00:00" and meeting["duration_min"] == 45
    invite = env.mailer.sent[-1]
    assert invite["subject"].startswith("[미팅 초대]") and "10월 29일(목) 15:00" in invite["body"]
    assert env.service.set_stage(l["id"], "성사", force=True) and env.service.find_lead("#1")["stage"] == "won"
    with pytest.raises(ValueError, match="없는 단계"):
        env.service.set_stage(l["id"], "아무거나")


def test_stats_plan_and_dashboard(env):
    emails = add_and_draft(env, lead(), lead(name="Tom Choi", company="Beta", email="tom@beta.com"))
    for e in emails:
        env.service.send_email(e["id"])
    env.service.log_call("Jane Kim connected good chat")
    env.service.log_call("Tom Choi na")
    env.service.book_meeting("Jane Kim 2026-10-28 16:00")

    s = env.service.stats(env.service.today())
    assert s["emails_sent"] == 2 and s["calls"] == 2 and s["connects"] == 1 and s["connect_rate"] == 50
    assert s["meetings_booked"] == 1 and s["meetings_held"] == 0 and s["new_leads"] == 2  # 16:00 is still ahead
    env.clock.advance(hours=7)
    assert env.service.stats(env.service.today())["meetings_held"] == 1
    env.clock.advance(hours=-7)

    agenda, plan = env.service.plan_for(env.service.today() + timedelta(days=1))
    assert plan.startswith("*Top 3*")
    context = env.ai.plans[-1]
    assert context["today_numbers"]["calls"] == 2 and len(context["today_call_reviews"]) == 2
    assert context["tasks"] == []  # next steps from today's calls are due in 2 days, not tomorrow
    later = env.service.agenda(env.service.today() + timedelta(days=2))
    assert any("Tom Choi" in t["title"] for t in later["tasks"])

    page = render(env.service)
    assert "오늘 통화" in page and "Tom Choi" in page
    assert fmt.stats_text("Today", s).count("*2*") >= 2
    assert "내일" in fmt.agenda_text(agenda, "내일")
    assert "Jane Kim" in fmt.lead_detail_text(env.service.lead_detail("#1"), env.service.local)


def test_update_contact_and_stage(env):
    add_and_draft(env, lead(email=""))
    with pytest.raises(ValueError, match="이메일 주소가 없어요"):
        env.service.send_email(1)
    l = env.service.update_contact("Jane Kim jane@acme.com", "email")
    assert l["email"] == "jane@acme.com"
    assert env.service.set_stage(l["id"], "won") is True
    assert env.service.set_stage(l["id"], "contacted") is False  # never move backwards automatically


def test_chunks_respect_slack_limit():
    text = "\n".join("x" * 100 for _ in range(100))
    chunks = _chunks(text)
    assert all(len(c) <= 2900 for c in chunks) and "".join(chunks).count("x") == 10000


def test_ai_requests_use_fallbacks_and_continue_paused_turns():
    calls = []

    class Messages:
        def parse(self, **kw):
            calls.append(("parse", kw))
            return SimpleNamespace(stop_reason="end_turn", parsed_output=SimpleNamespace(leads=[lead(), lead(name=" ")]))

        def create(self, **kw):
            calls.append(("create", kw))
            reason = "pause_turn" if len(calls) == 2 else "end_turn"
            return SimpleNamespace(stop_reason=reason, content=[SimpleNamespace(type="text", text="brief")])

    client = SimpleNamespace(beta=SimpleNamespace(messages=Messages()))
    ai = SalesAI(Settings(), client=client)
    assert [l.name for l in ai.parse_leads("Jane")] == ["Jane Kim"]  # blank names dropped
    assert ai.research({"name": "Jane", "company": "Acme"}) == "brief"

    kind, kw = calls[0]
    assert kw["model"] == "claude-opus-5-5" and kw["fallbacks"] == "default"
    assert kw["betas"] == ["server-side-fallback-2026-07-01"]
    assert [c[0] for c in calls] == ["parse", "create", "create"]
    assert calls[2][1]["messages"][-1]["role"] == "assistant"
    assert calls[1][1]["tools"][0]["type"] == "web_search_20260209"
