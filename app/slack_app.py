"""Slack front end: /lead, /call, /meeting, /sales, draft approval buttons and scheduled jobs."""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any, Callable

from slack_bolt import App
from slack_sdk.errors import SlackApiError

from . import formatting as fmt
from .config import Settings
from .service import SalesService

log = logging.getLogger(__name__)

LEAD_HELP = """*타깃 추가* — 한 명 또는 여러 명을 아무 형식으로나 붙여넣으세요. 예:
`/lead 김지현 루미에르몰 대표 jihyun@lumieremall.co.kr lumieremall.co.kr — 리테일 서밋에서 만남`
사람과 회사를 리서치한 뒤, 맞춤 이메일 초안을 여기에 올려 드릴게요. 보내기 전에 꼭 확인을 받습니다."""

CALL_HELP = """*통화 기록* — `/call <리드> [결과] <메모>`
결과: `연결` · `음성` · `부재` · `미팅` · `거절` (생략하면 메모를 보고 판단해요)
예: `/call 김지현 연결 일본 배송비에 관심 많음, 견적 원함, 다음 주 화요일 다시 통화`"""

SALES_HELP = """*세일즈 에이전트 명령어*
• `/lead <사람들>` — 타깃 추가 → 리서치 + 맞춤 이메일 초안 (승인 후 발송)
• `/call <리드> [연결|음성|부재|미팅|거절] <메모>` — 통화 기록 → 요약, 코칭, 다음 할 일
• `/meeting <리드> YYYY-MM-DD HH:MM [분]` — 미팅 등록 + 캘린더 초대 메일 (`내일 14:00`도 돼요)
• `/sales 오늘` · `/sales 주간` — 통화, 연결률, 보낸 메일, 답장, 미팅 숫자
• `/sales 내일` — 내일 미팅, 할 일, 우선순위 계획
• `/sales 파이프라인` — 단계별 리드
• `/sales 리드 <리드>` — 한 사람의 전체 기록
• `/sales 완료 <할 일 번호>` — 할 일 완료 처리
• `/sales 단계 <리드> <단계>` — 단계 변경 (신규, 초안, 연락함, 대화중, 미팅, 성사, 종료)
• `/sales 이메일 <리드> <주소>` · `/sales 전화 <리드> <번호>` — 연락처 수정
• `/sales 팔로업` — 지금 보낼 차례인 팔로업 초안 만들기
• `/sales 리포트` — 오늘의 리포트를 지금 받기
리드는 `#12`, 이름, 회사명, 이메일로 부를 수 있어요. 이름이 겹치면 `이름 | 메모` 형식으로 쓰세요."""

# Korean and English subcommands both work.
SUBCOMMANDS = {
    "도움말": "help", "help": "help",
    "오늘": "today", "today": "today",
    "주간": "week", "이번주": "week", "week": "week",
    "파이프라인": "pipeline", "pipeline": "pipeline",
    "내일": "tomorrow", "계획": "tomorrow", "tomorrow": "tomorrow", "plan": "tomorrow",
    "리포트": "report", "보고서": "report", "report": "report",
    "팔로업": "followups", "followups": "followups",
    "리드": "lead", "lead": "lead",
    "완료": "done", "done": "done",
    "단계": "stage", "stage": "stage",
    "이메일": "email", "email": "email",
    "전화": "phone", "phone": "phone",
}


def create_app(service: SalesService, settings: Settings) -> tuple[App, dict[str, Callable[[], None]]]:
    """Build the Bolt app and the scheduled jobs (follow-up drafts, reply checks, daily report)."""
    app = App(token=settings.slack_bot_token)
    pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="sales")

    def post(channel: str, text: str, blocks: list[dict[str, Any]] | None = None) -> None:
        targets = [c for c in dict.fromkeys([channel, settings.slack_channel_id]) if c]
        for target in targets:
            try:
                app.client.chat_postMessage(channel=target, text=text, blocks=blocks, unfurl_links=False)
                return
            except SlackApiError as exc:
                log.warning("Posting to %s failed: %s", target, exc.response.get("error"))
        log.error("Could not post message: %s", text[:200])

    def background(respond: Callable[..., Any], work: Callable[[], None]) -> None:
        def run() -> None:
            try:
                work()
            except (LookupError, ValueError) as exc:
                respond(text=f"⚠️ {exc}")
            except Exception as exc:  # report anything unexpected back to the user
                log.exception("Background job failed")
                respond(text=f"⚠️ 문제가 생겼어요: {exc}")

        pool.submit(run)

    def post_draft(channel: str, lead: dict[str, Any], email: dict[str, Any]) -> None:
        post(channel, f"{lead['name']}님 이메일 초안", fmt.draft_blocks(lead, email))

    # ------------------------------------------------------------ /lead

    @app.command("/lead")
    def lead_command(ack, command, respond):
        text = (command.get("text") or "").strip()
        if not text:
            ack(LEAD_HELP)
            return
        ack("🔎 받았어요. 한 명씩 리서치하고 있어요. 이메일 초안이 곧 여기에 올라옵니다.")
        channel = command["channel_id"]

        def work() -> None:
            results = service.add_leads(text, channel)
            if not results:
                respond(text="메시지에서 사람을 찾지 못했어요. 최소한 이름과 회사를 적어 주세요.")
                return
            for lead, created in results:
                if not created:
                    post(channel, f"ℹ️ {fmt.lead_line(lead)}은(는) 이미 CRM에 있어요 (단계: {fmt.stage(lead['stage'])}).")
                    continue
                try:
                    email = service.research_and_draft(lead["id"])
                except Exception as exc:
                    log.exception("Drafting failed for lead %s", lead["id"])
                    post(channel, f"⚠️ {fmt.lead_line(lead)}은(는) 저장했지만 초안 작성에 실패했어요: {exc}")
                    continue
                post_draft(channel, service.db.get("leads", lead["id"]), email)

        background(respond, work)

    # ------------------------------------------------------------ draft buttons

    def update_card(body: dict[str, Any], text: str, blocks: list[dict[str, Any]]) -> None:
        app.client.chat_update(channel=body["channel"]["id"], ts=body["message"]["ts"], text=text, blocks=blocks)

    def email_id_from(body: dict[str, Any]) -> int:
        return int(body["actions"][0]["value"])

    @app.action("send_email")
    def send_email(ack, body, respond):
        ack()

        def work() -> None:
            email, lead = service.send_email(email_id_from(body))
            update_card(body, f"{lead['name']}님께 발송 완료", fmt.sent_blocks(lead, email, body["user"]["id"]))

        background(lambda **kw: respond(replace_original=False, response_type="ephemeral", **kw), work)

    @app.action("skip_email")
    def skip_email(ack, body, respond):
        ack()
        try:
            email = service.skip_email(email_id_from(body))
        except (LookupError, ValueError) as exc:
            respond(text=f"⚠️ {exc}", replace_original=False, response_type="ephemeral")
            return
        lead = service.db.get("leads", email["lead_id"])
        update_card(body, "초안 건너뜀", fmt.skipped_blocks(lead, email))

    @app.action("regenerate_email")
    def regenerate_email(ack, body, respond):
        ack()

        def work() -> None:
            email = service.regenerate(email_id_from(body))
            lead = service.db.get("leads", email["lead_id"])
            update_card(body, f"{lead['name']}님 새 초안", fmt.draft_blocks(lead, email))

        background(lambda **kw: respond(replace_original=False, response_type="ephemeral", **kw), work)

    @app.action("edit_email")
    def edit_email(ack, body, client):
        ack()
        email = service.db.get("emails", email_id_from(body))
        metadata = json.dumps({"email_id": email["id"], "channel": body["channel"]["id"], "ts": body["message"]["ts"]})
        client.views_open(trigger_id=body["trigger_id"], view=fmt.edit_modal(email, metadata))

    @app.view("edit_email_submit")
    def edit_email_submit(ack, body, view, client):
        values = view["state"]["values"]
        subject = values["subject"]["value"]["value"]
        text = values["body"]["value"]["value"]
        meta = json.loads(view["private_metadata"])
        user = body["user"]["id"]
        try:
            service.update_draft(meta["email_id"], subject, text)
        except (LookupError, ValueError) as exc:
            ack(response_action="errors", errors={"body": str(exc)[:150]})
            return
        ack()  # close the modal now; sending over SMTP can take longer than Slack's 3 second limit

        def work() -> None:
            email, lead = service.send_email(meta["email_id"])
            client.chat_update(
                channel=meta["channel"], ts=meta["ts"], text=f"{lead['name']}님께 발송 완료",
                blocks=fmt.sent_blocks(lead, email, user),
            )

        background(lambda text: client.chat_postEphemeral(channel=meta["channel"], user=user, text=text), work)

    # ------------------------------------------------------------ /call

    @app.command("/call")
    def call_command(ack, command, respond):
        text = (command.get("text") or "").strip()
        if not text:
            ack(CALL_HELP)
            return
        ack("📞 통화를 기록하고 있어요…")

        def work() -> None:
            lead, review, task = service.log_call(text)
            respond(response_type="in_channel", text=fmt.call_text(lead, review, task))

        background(respond, work)

    # ------------------------------------------------------------ /meeting

    @app.command("/meeting")
    def meeting_command(ack, command, respond):
        text = (command.get("text") or "").strip()
        ack("📅 미팅을 등록하고 있어요…")

        def work() -> None:
            lead, meeting, invited = service.book_meeting(text)
            start = service.local(meeting["starts_at"])
            respond(response_type="in_channel", text=fmt.meeting_text(lead, start, meeting["duration_min"], invited))

        background(respond, work)

    # ------------------------------------------------------------ /sales

    @app.command("/sales")
    def sales_command(ack, command, respond):
        words = (command.get("text") or "오늘").strip().split(maxsplit=1)
        sub, rest = SUBCOMMANDS.get(words[0].lower(), words[0].lower()), (words[1] if len(words) > 1 else "")
        today = service.today()

        if sub == "help":
            ack(SALES_HELP)
        elif sub == "today":
            ack(fmt.stats_text(f"오늘 ({today})", service.stats(today)) + "\n\n" + fmt.agenda_text(service.agenda(today), "오늘 남은 일"))
        elif sub == "week":
            start = today - timedelta(days=6)
            ack(fmt.stats_text(f"최근 7일 ({start} ~ {today})", service.stats(start, 7)))
        elif sub == "pipeline":
            ack(fmt.pipeline_text(service.pipeline()))
        elif sub == "tomorrow":
            ack("🗓️ 내일 일정을 정리하고 있어요…")
            background(respond, lambda: respond(text=build_plan(today + timedelta(days=1))))
        elif sub == "report":
            ack("📊 리포트를 만들고 있어요…")
            background(respond, lambda: respond(response_type="in_channel", text=build_report()))
        elif sub == "followups":
            ack("✉️ 보낼 차례인 팔로업을 확인하고 있어요…")
            background(respond, lambda: respond(text=f"팔로업 초안 {run_followups()}개를 만들었어요."))
        else:
            try:
                ack(run_sales_edit(sub, rest))
            except (LookupError, ValueError) as exc:
                ack(f"⚠️ {exc}")

    def run_sales_edit(sub: str, rest: str) -> str:
        if sub == "lead":
            return fmt.lead_detail_text(service.lead_detail(rest), service.local)
        if sub == "done":
            number = rest.strip().lstrip("#")
            if not number.isdigit():
                raise ValueError("할 일 번호를 적어 주세요. 예: `/sales 완료 12`")
            task = service.complete_task(int(number))
            return f"✅ 완료: {task['title']}"
        if sub == "stage":
            lead, stage = service.split_lead_ref(rest)
            changed = service.set_stage(lead["id"], stage.strip().lower(), force=True)
            lead = service.db.get("leads", lead["id"])
            return f"{fmt.lead_line(lead)} → *{fmt.stage(lead['stage'])}*" if changed else "단계가 그대로예요."
        if sub in ("email", "phone"):
            lead = service.update_contact(rest, sub)
            label = "이메일" if sub == "email" else "전화번호"
            return f"{fmt.lead_line(lead)}의 {label}를 바꿨어요: {lead[sub]}"
        return SALES_HELP

    # ------------------------------------------------------------ jobs (also used by the scheduler)

    def build_plan(day) -> str:
        agenda, plan = service.plan_for(day)
        text = fmt.agenda_text(agenda, "내일")
        return text + ("\n\n*계획*\n" + plan if plan else "")

    def build_report() -> str:
        today = service.today()
        reviews = service.call_reviews(today)
        calls = "\n".join(
            f"• {fmt.esc(c['name'])} — {fmt.OUTCOME_LABEL.get(c['outcome'], c['outcome'])}: {fmt.esc(c['summary'])}" for c in reviews
        )
        parts = [
            "📊 *오늘의 영업 리포트*",
            fmt.stats_text(f"오늘 ({today})", service.stats(today)),
            ("*오늘 통화*\n" + calls) if calls else "",
            build_plan(today + timedelta(days=1)),
        ]
        return "\n\n".join(p for p in parts if p)

    def run_followups() -> int:
        drafts = service.draft_due_followups()
        for lead, email in drafts:
            post(lead["slack_channel"], f"{lead['name']}님 팔로업 초안", fmt.draft_blocks(lead, email, show_research=False))
        return len(drafts)

    def job_followups() -> None:
        run_followups()

    def job_replies() -> None:
        for lead, replies in service.check_replies():
            latest = replies[-1]
            post(
                lead["slack_channel"],
                f"💬 {lead['name']}님이 답장했어요!",
                [fmt.section(f"💬 *답장 도착* — {fmt.lead_line(lead)}\n*{fmt.esc(latest['subject'])}*\n> {fmt.esc(latest['snippet'])}\n"
                             f"팔로업 메일은 멈췄어요. `/meeting #{lead['id']} YYYY-MM-DD HH:MM`으로 미팅을 잡으세요.")],
            )

    def job_daily_report() -> None:
        post(settings.slack_channel_id, "오늘의 영업 리포트", [fmt.section(chunk) for chunk in _chunks(build_report())])

    return app, {"followups": job_followups, "replies": job_replies, "daily_report": job_daily_report}


def _chunks(text: str, size: int = 2900) -> list[str]:
    """Split long text on line breaks so each Slack section stays under its 3000-char limit."""
    chunks, current = [], ""
    for line in text.splitlines():
        if len(current) + len(line) + 1 > size and current:
            chunks.append(current)
            current = ""
        current += line + "\n"
    if current.strip():
        chunks.append(current)
    return chunks
