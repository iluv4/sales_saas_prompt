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
from .service import STAGES, SalesService

log = logging.getLogger(__name__)

LEAD_HELP = """*Add targets* — paste one or more people in any format, e.g.
`/lead Jane Kim, CEO of Acme Commerce, jane@acmecommerce.com, acmecommerce.com — met at Retail Summit`
I'll research each person and company, then post a personal email draft here for you to approve."""

SALES_HELP = """*Sales agent commands*
• `/lead <people>` — add targets; I research them and draft personal emails for your approval
• `/call <lead> [connected|vm|na|meeting|ni] <notes>` — log a call; I review it and set the next step
• `/meeting <lead> YYYY-MM-DD HH:MM [minutes]` — book a meeting and email a calendar invite
• `/sales today` · `/sales week` — activity numbers (calls, connects, emails, replies, meetings)
• `/sales tomorrow` — tomorrow's meetings, tasks and a prioritized plan
• `/sales pipeline` — leads by stage
• `/sales lead <lead>` — one lead's timeline
• `/sales done <task id>` — mark a task done
• `/sales stage <lead> <stage>` — move a lead (stages: """ + ", ".join(STAGES) + """)
• `/sales email <lead> <address>` · `/sales phone <lead> <number>` — fix contact details
• `/sales followups` — draft any follow-ups that are due now
A lead can be `#12`, a full name, a company or an email. Use `Name | notes` if a name is ambiguous."""


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
                respond(text=f"⚠️ Something went wrong: {exc}")

        pool.submit(run)

    def post_draft(channel: str, lead: dict[str, Any], email: dict[str, Any]) -> None:
        post(channel, f"Email draft for {lead['name']}", fmt.draft_blocks(lead, email))

    # ------------------------------------------------------------ /lead

    @app.command("/lead")
    def lead_command(ack, command, respond):
        text = (command.get("text") or "").strip()
        if not text:
            ack(LEAD_HELP)
            return
        ack("🔎 Got it. Reading your targets and researching each one; drafts will show up here.")
        channel = command["channel_id"]

        def work() -> None:
            results = service.add_leads(text, channel)
            if not results:
                respond(text="I couldn't find any people in that message. Include at least a name and company.")
                return
            for lead, created in results:
                if not created:
                    post(channel, f"ℹ️ {fmt.lead_line(lead)} is already in the CRM (stage: {lead['stage']}).")
                    continue
                try:
                    email = service.research_and_draft(lead["id"])
                except Exception as exc:
                    log.exception("Drafting failed for lead %s", lead["id"])
                    post(channel, f"⚠️ Saved {fmt.lead_line(lead)} but drafting failed: {exc}")
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
            update_card(body, f"Sent to {lead['name']}", fmt.sent_blocks(lead, email, body["user"]["id"]))

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
        update_card(body, "Draft skipped", fmt.skipped_blocks(lead, email))

    @app.action("regenerate_email")
    def regenerate_email(ack, body, respond):
        ack()

        def work() -> None:
            email = service.regenerate(email_id_from(body))
            lead = service.db.get("leads", email["lead_id"])
            update_card(body, f"New draft for {lead['name']}", fmt.draft_blocks(lead, email))

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
                channel=meta["channel"], ts=meta["ts"], text=f"Sent to {lead['name']}",
                blocks=fmt.sent_blocks(lead, email, user),
            )

        background(lambda text: client.chat_postEphemeral(channel=meta["channel"], user=user, text=text), work)

    # ------------------------------------------------------------ /call

    @app.command("/call")
    def call_command(ack, command, respond):
        text = (command.get("text") or "").strip()
        if not text:
            ack("Usage: `/call <lead> [connected|vm|na|meeting|ni] <what happened>`\n"
                "e.g. `/call Jane Kim connected she likes the idea, wants pricing, call back next Tuesday`")
            return
        ack("📞 Logging the call…")

        def work() -> None:
            lead, review, task = service.log_call(text)
            respond(response_type="in_channel", text=fmt.call_text(lead, review, task))

        background(respond, work)

    # ------------------------------------------------------------ /meeting

    @app.command("/meeting")
    def meeting_command(ack, command, respond):
        text = (command.get("text") or "").strip()
        ack("📅 Booking…")

        def work() -> None:
            lead, meeting, invited = service.book_meeting(text)
            when = service.local(meeting["starts_at"])
            invite = "Calendar invite emailed ✅" if invited else "No invite sent (no email address or SMTP not set up)"
            respond(
                response_type="in_channel",
                text=f"📅 *Meeting booked* with {fmt.lead_line(lead)}\n{when:%a %Y-%m-%d %H:%M} · "
                     f"{meeting['duration_min']} min · {invite}",
            )

        background(respond, work)

    # ------------------------------------------------------------ /sales

    @app.command("/sales")
    def sales_command(ack, command, respond):
        words = (command.get("text") or "today").strip().split(maxsplit=1)
        sub, rest = words[0].lower(), (words[1] if len(words) > 1 else "")
        today = service.today()

        if sub == "help":
            ack(SALES_HELP)
        elif sub == "today":
            ack(fmt.stats_text(f"Today ({today})", service.stats(today)) + "\n\n" + fmt.agenda_text(service.agenda(today), "Still to do today"))
        elif sub == "week":
            start = today - timedelta(days=6)
            ack(fmt.stats_text(f"Last 7 days ({start} → {today})", service.stats(start, 7)))
        elif sub == "pipeline":
            ack(fmt.pipeline_text(service.pipeline()))
        elif sub in ("tomorrow", "plan"):
            ack("🗓️ Putting tomorrow together…")
            background(respond, lambda: respond(text=build_plan(today + timedelta(days=1))))
        elif sub == "report":
            ack("📊 Building the report…")
            background(respond, lambda: respond(response_type="in_channel", text=build_report()))
        elif sub == "followups":
            ack("✉️ Checking for due follow-ups…")
            background(respond, lambda: respond(text=f"Drafted {run_followups()} follow-up(s)."))
        else:
            try:
                ack(run_sales_edit(sub, rest))
            except (LookupError, ValueError) as exc:
                ack(f"⚠️ {exc}")

    def run_sales_edit(sub: str, rest: str) -> str:
        if sub == "lead":
            return fmt.lead_detail_text(service.lead_detail(rest), service.local)
        if sub == "done":
            task = service.complete_task(int(rest.strip().lstrip("#")))
            return f"✅ Done: {task['title']}"
        if sub == "stage":
            lead, stage = service.split_lead_ref(rest)
            changed = service.set_stage(lead["id"], stage.strip().lower(), force=True)
            return f"{fmt.lead_line(lead)} → *{stage.strip().lower()}*" if changed else "Stage unchanged."
        if sub in ("email", "phone"):
            lead = service.update_contact(rest, sub)
            return f"Updated {sub} for {fmt.lead_line(lead)}: {lead[sub]}"
        return SALES_HELP

    # ------------------------------------------------------------ jobs (also used by the scheduler)

    def build_plan(day) -> str:
        agenda, plan = service.plan_for(day)
        text = fmt.agenda_text(agenda, "Tomorrow")
        return text + ("\n\n*Plan*\n" + plan if plan else "")

    def build_report() -> str:
        today = service.today()
        reviews = service.call_reviews(today)
        calls = "\n".join(
            f"• {fmt.esc(c['name'])} — {fmt.OUTCOME_LABEL.get(c['outcome'], c['outcome'])}: {fmt.esc(c['summary'])}" for c in reviews
        )
        parts = [
            "📊 *Daily sales report*",
            fmt.stats_text(f"Today ({today})", service.stats(today)),
            ("*Today's calls*\n" + calls) if calls else "",
            build_plan(today + timedelta(days=1)),
        ]
        return "\n\n".join(p for p in parts if p)

    def run_followups() -> int:
        drafts = service.draft_due_followups()
        for lead, email in drafts:
            post(lead["slack_channel"], f"Follow-up draft for {lead['name']}", fmt.draft_blocks(lead, email, show_research=False))
        return len(drafts)

    def job_followups() -> None:
        run_followups()

    def job_replies() -> None:
        for lead, replies in service.check_replies():
            latest = replies[-1]
            post(
                lead["slack_channel"],
                f"💬 {lead['name']} replied!",
                [fmt.section(f"💬 *Reply from* {fmt.lead_line(lead)}\n*{fmt.esc(latest['subject'])}*\n> {fmt.esc(latest['snippet'])}\n"
                             f"Follow-ups stopped. Book it with `/meeting #{lead['id']} YYYY-MM-DD HH:MM`.")],
            )

    def job_daily_report() -> None:
        post(settings.slack_channel_id, "Daily sales report", [fmt.section(chunk) for chunk in _chunks(build_report())])

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
