"""Core sales workflow, independent of Slack: leads -> research -> email -> follow-ups -> calls -> meetings."""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable

from .ai import AIError
from .config import Settings
from .db import DB
from .mailer import build_ics
from .research import fetch_site_text

log = logging.getLogger(__name__)

STAGES = ["new", "drafted", "contacted", "engaged", "meeting_booked", "won", "lost"]
STAGE_RANK = {stage: i for i, stage in enumerate(STAGES)}

OUTCOME_ALIASES = {
    "connected": "connected", "talked": "connected", "spoke": "connected", "conn": "connected",
    "voicemail": "voicemail", "vm": "voicemail", "left-vm": "voicemail",
    "no-answer": "no_answer", "no_answer": "no_answer", "noanswer": "no_answer", "na": "no_answer",
    "meeting": "meeting_booked", "booked": "meeting_booked", "meeting_booked": "meeting_booked",
    "not-interested": "not_interested", "not_interested": "not_interested", "ni": "not_interested",
    "wrong-number": "wrong_number", "wrong": "wrong_number",
}
CONNECT_OUTCOMES = {"connected", "meeting_booked", "not_interested"}


class LeadNotFound(LookupError):
    def __init__(self, ref: str, suggestions: list[dict[str, Any]]):
        self.ref = ref
        self.suggestions = suggestions
        names = ", ".join(f"#{l['id']} {l['name']} ({l['company']})" for l in suggestions)
        super().__init__(f"No lead matches '{ref}'." + (f" Did you mean: {names}?" if names else ""))


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


class SalesService:
    def __init__(self, db: DB, ai: Any, mailer: Any, settings: Settings, clock: Callable[[], datetime] | None = None):
        self.db = db
        self.ai = ai
        self.mailer = mailer
        self.s = settings
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    # ------------------------------------------------------------ time helpers

    def now(self) -> datetime:
        return self.clock()

    def today(self) -> date:
        return self.now().astimezone(self.s.timezone).date()

    def day_range(self, start: date, days: int = 1) -> tuple[str, str]:
        begin = datetime.combine(start, time.min, self.s.timezone)
        return iso(begin), iso(begin + timedelta(days=days))

    def local(self, value: str) -> datetime:
        return parse_iso(value).astimezone(self.s.timezone)

    # ------------------------------------------------------------ leads

    def add_leads(self, text: str, channel: str = "") -> list[tuple[dict[str, Any], bool]]:
        """Parse free text into leads. Returns (lead, created); existing emails are not duplicated."""
        results = []
        for parsed in self.ai.parse_leads(text):
            data = parsed.model_dump()
            if data["email"]:
                existing = self.db.one("SELECT * FROM leads WHERE lower(email) = lower(?)", (data["email"],))
                if existing:
                    results.append((existing, False))
                    continue
            now = iso(self.now())
            lead_id = self.db.insert("leads", **data, stage="new", slack_channel=channel, created_at=now, updated_at=now)
            results.append((self.db.get("leads", lead_id), True))
        return results

    def find_lead(self, ref: str) -> dict[str, Any]:
        ref = ref.strip()
        match = re.fullmatch(r"#?(\d+)", ref)
        if match:
            lead = self.db.get("leads", int(match.group(1)))
            if lead:
                return lead
        exact = self.db.query(
            "SELECT * FROM leads WHERE lower(name) = lower(?) OR lower(email) = lower(?) OR lower(company) = lower(?)",
            (ref, ref, ref),
        )
        if len(exact) == 1:
            return exact[0]
        like = f"%{ref.lower()}%"
        fuzzy = self.db.query(
            "SELECT * FROM leads WHERE lower(name) LIKE ? OR lower(company) LIKE ? OR lower(email) LIKE ? ORDER BY id",
            (like, like, like),
        )
        if len(fuzzy) == 1:
            return fuzzy[0]
        raise LeadNotFound(ref, (exact or fuzzy)[:5])

    def split_lead_ref(self, text: str) -> tuple[dict[str, Any], str]:
        """Split '<lead> rest of text'. The lead is '#12', 'Name | rest', or the longest leading name/company match."""
        text = text.strip()
        if "|" in text:
            ref, rest = text.split("|", 1)
            return self.find_lead(ref), rest.strip()
        words = text.split()
        if words and re.fullmatch(r"#\d+", words[0]):
            return self.find_lead(words[0]), " ".join(words[1:])
        for k in range(len(words), 0, -1):
            candidate = " ".join(words[:k])
            rows = self.db.query(
                "SELECT * FROM leads WHERE lower(name) = lower(?) OR lower(company) = lower(?) OR lower(email) = lower(?)",
                (candidate, candidate, candidate),
            )
            if len(rows) == 1:
                return rows[0], " ".join(words[k:])
        raise LeadNotFound(words[0] if words else "", self._suggest(words[0] if words else ""))

    def _suggest(self, word: str) -> list[dict[str, Any]]:
        if not word:
            return []
        like = f"%{word.lower()}%"
        return self.db.query("SELECT * FROM leads WHERE lower(name) LIKE ? OR lower(company) LIKE ? LIMIT 5", (like, like))

    def update_contact(self, text: str, field: str) -> dict[str, Any]:
        """'/sales email <lead> jane@acme.com' or '/sales phone <lead> +82 10 ...'."""
        if field not in ("email", "phone"):
            raise ValueError(f"Can't update '{field}'.")
        lead, value = self.split_lead_ref(text)
        if not value:
            raise ValueError(f"Give the new {field} after the lead.")
        self.db.update("leads", lead["id"], **{field: value.strip(), "updated_at": iso(self.now())})
        return self.db.get("leads", lead["id"])

    def set_stage(self, lead_id: int, stage: str, force: bool = False) -> bool:
        """Move a lead forward in the pipeline (never backwards unless forced). won/lost always apply."""
        if stage not in STAGE_RANK:
            raise ValueError(f"Unknown stage '{stage}'. Use one of: {', '.join(STAGES)}")
        lead = self.db.get("leads", lead_id)
        current = lead["stage"]
        if current == stage:
            return False
        if not force and stage not in ("won", "lost") and (current in ("won", "lost") or STAGE_RANK[stage] < STAGE_RANK[current]):
            return False
        now = iso(self.now())
        self.db.update("leads", lead_id, stage=stage, updated_at=now)
        self.db.insert("activities", lead_id=lead_id, type="stage_change", outcome=stage, summary=f"{current} → {stage}", created_at=now)
        return True

    # ------------------------------------------------------------ tasks

    def add_task(self, lead_id: int, kind: str, title: str, due: date, step: int = 0) -> int:
        return self.db.insert(
            "tasks", lead_id=lead_id, kind=kind, title=title, due_date=due.isoformat(), step=step,
            status="open", created_at=iso(self.now()),
        )

    def cancel_tasks(self, lead_id: int, kinds: tuple[str, ...]) -> None:
        marks = ",".join("?" for _ in kinds)
        self.db.execute(
            f"UPDATE tasks SET status = 'canceled', done_at = ? WHERE lead_id = ? AND status = 'open' AND kind IN ({marks})",
            [iso(self.now()), lead_id, *kinds],
        )

    def complete_task(self, task_id: int) -> dict[str, Any]:
        task = self.db.get("tasks", task_id)
        if not task:
            raise LookupError(f"No task #{task_id}.")
        self.db.update("tasks", task_id, status="done", done_at=iso(self.now()))
        return task

    # ------------------------------------------------------------ emails

    def compose_body(self, body: str) -> str:
        parts = [body.strip()]
        if self.s.signature:
            parts.append(self.s.signature)
        if self.s.opt_out_line:
            parts.append(self.s.opt_out_line)
        return "\n\n".join(parts)

    def research_and_draft(self, lead_id: int) -> dict[str, Any]:
        lead = self.db.get("leads", lead_id)
        if self.s.research_mode == "web":
            raw_research = self.ai.research(lead)
        else:
            site = fetch_site_text(lead["website"])
            raw_research = f"Homepage text of {lead['website']}:\n{site}" if site else ""
        draft = self.ai.draft_intro(lead, raw_research)
        hooks = "\n".join(f"• {hook}" for hook in draft.personalization_hooks)
        research = f"{draft.company_summary}\n\n{draft.person_insights}\n\nHooks:\n{hooks}".strip()
        self.db.update("leads", lead_id, research=research, updated_at=iso(self.now()))
        self.set_stage(lead_id, "drafted")
        email_id = self.db.insert(
            "emails", lead_id=lead_id, step=0, subject=draft.subject.strip(), body=self.compose_body(draft.body),
            status="draft", created_at=iso(self.now()),
        )
        return self.db.get("emails", email_id)

    def regenerate(self, email_id: int) -> dict[str, Any]:
        email = self._draft(email_id)
        self.db.update("emails", email_id, status="skipped")
        if email["step"] == 0:
            return self.research_and_draft(email["lead_id"])
        return self._draft_followup(self.db.get("leads", email["lead_id"]), email["step"])

    def update_draft(self, email_id: int, subject: str, body: str) -> dict[str, Any]:
        self._draft(email_id)
        self.db.update("emails", email_id, subject=subject.strip(), body=body.strip())
        return self.db.get("emails", email_id)

    def skip_email(self, email_id: int) -> dict[str, Any]:
        self._draft(email_id)
        self.db.update("emails", email_id, status="skipped")
        return self.db.get("emails", email_id)

    def _draft(self, email_id: int) -> dict[str, Any]:
        email = self.db.get("emails", email_id)
        if not email:
            raise LookupError(f"No email #{email_id}.")
        if email["status"] != "draft":
            raise ValueError(f"This email was already {email['status']}.")
        return email

    def emails_sent_today(self) -> int:
        start, end = self.day_range(self.today())
        return self.db.scalar(
            "SELECT count(*) FROM activities WHERE type = 'email_sent' AND created_at >= ? AND created_at < ?", (start, end)
        )

    def send_email(self, email_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
        email = self._draft(email_id)
        lead = self.db.get("leads", email["lead_id"])
        if not lead["email"]:
            raise ValueError(f"{lead['name']} has no email address. Add one with `/sales email #{lead['id']} name@company.com`.")
        if self.emails_sent_today() >= self.s.daily_send_limit:
            raise ValueError(f"Daily send limit reached ({self.s.daily_send_limit}). It protects your domain's reputation.")
        message_id = self.mailer.send(lead["email"], email["subject"], email["body"], in_reply_to=email["in_reply_to"])
        now = iso(self.now())
        self.db.update("emails", email_id, status="sent", message_id=message_id, sent_at=now)
        self.db.insert(
            "activities", lead_id=lead["id"], type="email_sent", outcome=f"step_{email['step']}",
            summary=email["subject"], created_at=now,
        )
        self.set_stage(lead["id"], "contacted")
        self._schedule_next_touch(lead, email["step"])
        return self.db.get("emails", email_id), self.db.get("leads", lead["id"])

    def _schedule_next_touch(self, lead: dict[str, Any], step: int) -> None:
        self.cancel_tasks(lead["id"], ("followup_email",))
        intervals = self.s.followup_intervals
        if step < len(intervals):
            due = self.today() + timedelta(days=intervals[step])
            self.add_task(lead["id"], "followup_email", f"Follow-up email #{step + 1} to {lead['name']}", due, step=step + 1)
        else:
            due = self.today() + timedelta(days=self.s.call_after_days)
            self.add_task(lead["id"], "call", f"Call {lead['name']} ({lead['company']}): no reply after {step + 1} emails", due)

    def draft_due_followups(self) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        """Turn follow-up tasks that are due into drafts waiting for approval."""
        due = self.db.query(
            "SELECT * FROM tasks WHERE kind = 'followup_email' AND status = 'open' AND due_date <= ? ORDER BY due_date",
            (self.today().isoformat(),),
        )
        drafts = []
        for task in due:
            lead = self.db.get("leads", task["lead_id"])
            if lead["stage"] != "contacted":
                self.db.update("tasks", task["id"], status="canceled", done_at=iso(self.now()))
                continue
            try:
                email = self._draft_followup(lead, task["step"])
            except AIError as exc:
                log.warning("Follow-up draft failed for lead %s: %s", lead["id"], exc)
                continue
            self.db.update("tasks", task["id"], status="done", done_at=iso(self.now()))
            drafts.append((lead, email))
        return drafts

    def _draft_followup(self, lead: dict[str, Any], step: int) -> dict[str, Any]:
        sent = self.db.query("SELECT * FROM emails WHERE lead_id = ? AND status = 'sent' ORDER BY sent_at", (lead["id"],))
        if not sent:
            raise ValueError(f"No email has been sent to {lead['name']} yet.")
        final = step >= len(self.s.followup_intervals)
        draft = self.ai.draft_followup(lead, sent, step, final)
        first_subject = sent[0]["subject"]
        subject = first_subject if first_subject.lower().startswith("re:") else f"Re: {first_subject}"
        email_id = self.db.insert(
            "emails", lead_id=lead["id"], step=step, subject=subject, body=self.compose_body(draft.body),
            status="draft", in_reply_to=sent[-1]["message_id"], created_at=iso(self.now()),
        )
        return self.db.get("emails", email_id)

    def check_replies(self) -> list[tuple[dict[str, Any], list[dict[str, str]]]]:
        """Look for inbox replies from contacted leads; move them to 'engaged' and stop the sequence."""
        if not self.mailer.can_read:
            return []
        leads = self.db.query("SELECT * FROM leads WHERE stage = 'contacted' AND email != ''")
        since: dict[str, datetime] = {}
        for lead in leads:
            first = self.db.scalar("SELECT min(sent_at) FROM emails WHERE lead_id = ? AND status = 'sent'", (lead["id"],))
            if first:
                since[lead["email"].lower()] = self.local(first)
        replies = self.mailer.find_replies(since)
        results = []
        for lead in leads:
            found = replies.get(lead["email"].lower())
            if not found:
                continue
            latest = found[-1]
            now = iso(self.now())
            self.db.insert(
                "activities", lead_id=lead["id"], type="reply", summary=latest["subject"], notes=latest["snippet"], created_at=now
            )
            self.set_stage(lead["id"], "engaged")
            self.cancel_tasks(lead["id"], ("followup_email",))
            self.add_task(lead["id"], "reply", f"Reply to {lead['name']} and book a meeting", self.today())
            results.append((self.db.get("leads", lead["id"]), found))
        return results

    # ------------------------------------------------------------ calls

    def log_call(self, text: str) -> tuple[dict[str, Any], Any, dict[str, Any] | None]:
        """'/call <lead> [outcome] notes...' -> AI review, activity, stage change and next task."""
        lead, rest = self.split_lead_ref(text)
        words = rest.split()
        hint = ""
        if words and words[0].lower().strip(",:") in OUTCOME_ALIASES:
            hint = OUTCOME_ALIASES[words[0].lower().strip(",:")]
            rest = " ".join(words[1:])
        notes = rest.strip(" -:,")
        review = self.ai.review_call(lead, notes, hint, self._history_text(lead["id"]))
        outcome = hint or review.outcome
        now = iso(self.now())
        self.db.insert(
            "activities", lead_id=lead["id"], type="call", outcome=outcome, notes=notes, summary=review.summary,
            data=json.dumps(review.model_dump(), ensure_ascii=False), created_at=now,
        )
        if outcome in CONNECT_OUTCOMES:
            self.cancel_tasks(lead["id"], ("followup_email", "call"))
        if outcome == "meeting_booked":
            self.set_stage(lead["id"], "meeting_booked")
        elif outcome == "not_interested":
            self.set_stage(lead["id"], "lost")
        elif outcome == "connected":
            self.set_stage(lead["id"], "engaged")
        task = None
        if review.next_step_kind != "none" and outcome != "not_interested" and review.next_step:
            due = self.today() + timedelta(days=max(1, review.next_step_in_days))
            task_id = self.add_task(lead["id"], review.next_step_kind, f"{lead['name']}: {review.next_step}", due)
            task = self.db.get("tasks", task_id)
        return self.db.get("leads", lead["id"]), review, task

    def _history_text(self, lead_id: int, limit: int = 8) -> str:
        rows = self.db.query(
            "SELECT * FROM activities WHERE lead_id = ? AND type != 'stage_change' ORDER BY created_at DESC LIMIT ?",
            (lead_id, limit),
        )
        return "\n".join(
            f"{self.local(r['created_at']):%Y-%m-%d} {r['type']} {r['outcome']}: {r['summary'] or r['notes']}"
            for r in reversed(rows)
        )

    # ------------------------------------------------------------ meetings

    def parse_when(self, text: str) -> tuple[str, datetime, int]:
        """'<lead> 2026-11-03 14:00 [45]' or '<lead> tomorrow 10:30' -> (lead text, local start, minutes)."""
        match = re.search(r"\s(today|tomorrow|\d{4}-\d{2}-\d{2})[ T](\d{1,2}:\d{2})(?:\s+(\d+))?\s*$", " " + text.strip())
        if not match:
            raise ValueError("Use: `/meeting <lead> YYYY-MM-DD HH:MM [minutes]` (or `tomorrow 14:00`).")
        day_word, clock, minutes = match.groups()
        if day_word == "today":
            day = self.today()
        elif day_word == "tomorrow":
            day = self.today() + timedelta(days=1)
        else:
            day = date.fromisoformat(day_word)
        hour, minute = (int(x) for x in clock.split(":"))
        start = datetime.combine(day, time(hour, minute), self.s.timezone)
        ref = (" " + text.strip())[: match.start()].strip()
        return ref, start, int(minutes or 30)

    def book_meeting(self, text: str) -> tuple[dict[str, Any], dict[str, Any], bool]:
        ref, start, minutes = self.parse_when(text)
        lead = self.find_lead(ref) if ref else None
        if not lead:
            raise ValueError("Tell me who the meeting is with: `/meeting <lead> YYYY-MM-DD HH:MM`.")
        title = f"{self.s.sender_company or 'Intro'} <> {lead['company'] or lead['name']}"
        now = iso(self.now())
        meeting_id = self.db.insert(
            "meetings", lead_id=lead["id"], starts_at=iso(start), duration_min=minutes, title=title, created_at=now
        )
        self.db.insert(
            "activities", lead_id=lead["id"], type="meeting_booked", summary=f"{title} on {start:%Y-%m-%d %H:%M}", created_at=now
        )
        self.set_stage(lead["id"], "meeting_booked")
        self.cancel_tasks(lead["id"], ("followup_email", "reply"))
        invite_sent = False
        if self.s.send_calendar_invites and lead["email"] and self.mailer.can_send:
            ics = build_ics(
                uid=f"{uuid.uuid4()}@sales-agent", start=start, minutes=minutes, summary=title,
                description=f"Meeting with {lead['name']} ({lead['company']}).", location=self.s.meeting_link,
                organizer_name=self.s.sender_name, organizer_email=self.s.sender_email,
                attendee_name=lead["name"], attendee_email=lead["email"], now=self.now(),
            )
            body = self.compose_body(
                f"Hi {lead['name'].split()[0]},\n\nThanks for making the time. I've sent a calendar invite for "
                f"{start:%A, %B %d at %H:%M} ({self.s.timezone.key})"
                + (f".\n\nJoin here: {self.s.meeting_link}" if self.s.meeting_link else ".")
                + "\n\nLooking forward to it."
            )
            self.mailer.send(lead["email"], f"Invitation: {title}", body, ics=ics)
            invite_sent = True
        return self.db.get("leads", lead["id"]), self.db.get("meetings", meeting_id), invite_sent

    # ------------------------------------------------------------ reporting

    def stats(self, start: date, days: int = 1) -> dict[str, Any]:
        lo, hi = self.day_range(start, days)

        def count(sql: str) -> int:
            return self.db.scalar(sql, (lo, hi)) or 0

        calls = self.db.query(
            "SELECT outcome, count(*) AS n FROM activities WHERE type = 'call' AND created_at >= ? AND created_at < ? GROUP BY outcome",
            (lo, hi),
        )
        by_outcome = {row["outcome"]: row["n"] for row in calls}
        total_calls = sum(by_outcome.values())
        connects = sum(n for outcome, n in by_outcome.items() if outcome in CONNECT_OUTCOMES)
        return {
            "new_leads": count("SELECT count(*) FROM leads WHERE created_at >= ? AND created_at < ?"),
            "emails_sent": count("SELECT count(*) FROM activities WHERE type = 'email_sent' AND created_at >= ? AND created_at < ?"),
            "replies": count("SELECT count(*) FROM activities WHERE type = 'reply' AND created_at >= ? AND created_at < ?"),
            "calls": total_calls,
            "connects": connects,
            "connect_rate": round(100 * connects / total_calls) if total_calls else 0,
            "calls_by_outcome": by_outcome,
            "meetings_booked": count("SELECT count(*) FROM activities WHERE type = 'meeting_booked' AND created_at >= ? AND created_at < ?"),
            # Only meetings whose start time has already passed count as held.
            "meetings_held": self.db.scalar(
                "SELECT count(*) FROM meetings WHERE status = 'scheduled' AND starts_at >= ? AND starts_at < ?",
                (lo, min(hi, iso(self.now()))),
            ) or 0,
        }

    def call_reviews(self, start: date, days: int = 1) -> list[dict[str, Any]]:
        lo, hi = self.day_range(start, days)
        rows = self.db.query(
            "SELECT a.*, l.name, l.company FROM activities a JOIN leads l ON l.id = a.lead_id "
            "WHERE a.type = 'call' AND a.created_at >= ? AND a.created_at < ? ORDER BY a.created_at",
            (lo, hi),
        )
        for row in rows:
            row["review"] = json.loads(row["data"] or "{}")
        return rows

    def pipeline(self) -> dict[str, list[dict[str, Any]]]:
        leads = self.db.query("SELECT * FROM leads ORDER BY updated_at DESC")
        return {stage: [l for l in leads if l["stage"] == stage] for stage in STAGES}

    def agenda(self, day: date) -> dict[str, Any]:
        """Meetings on `day`, plus every open task due on or before it (overdue ones flagged)."""
        lo, hi = self.day_range(day)
        meetings = self.db.query(
            "SELECT m.*, l.name, l.company, l.title AS lead_title, l.research FROM meetings m JOIN leads l ON l.id = m.lead_id "
            "WHERE m.status = 'scheduled' AND m.starts_at >= ? AND m.starts_at < ? ORDER BY m.starts_at",
            (lo, hi),
        )
        for meeting in meetings:
            meeting["local_time"] = self.local(meeting["starts_at"]).strftime("%H:%M")
        tasks = self.db.query(
            "SELECT t.*, l.name, l.company, l.phone, l.email FROM tasks t JOIN leads l ON l.id = t.lead_id "
            "WHERE t.status = 'open' AND t.due_date <= ? ORDER BY t.due_date, t.id",
            (day.isoformat(),),
        )
        today = self.today().isoformat()
        for task in tasks:
            task["overdue"] = task["due_date"] < today
        pending = self.db.scalar("SELECT count(*) FROM emails WHERE status = 'draft'") or 0
        return {"date": day.isoformat(), "meetings": meetings, "tasks": tasks, "pending_drafts": pending}

    def lead_detail(self, ref: str) -> dict[str, Any]:
        lead = self.find_lead(ref)
        lid = lead["id"]
        return {
            "lead": lead,
            "activities": self.db.query("SELECT * FROM activities WHERE lead_id = ? ORDER BY created_at DESC LIMIT 15", (lid,)),
            "tasks": self.db.query("SELECT * FROM tasks WHERE lead_id = ? AND status = 'open' ORDER BY due_date", (lid,)),
            "meetings": self.db.query("SELECT * FROM meetings WHERE lead_id = ? ORDER BY starts_at DESC", (lid,)),
        }

    def plan_for(self, day: date) -> tuple[dict[str, Any], str]:
        """Agenda for `day` plus Claude's prioritized plan (empty string if Claude is unavailable)."""
        agenda = self.agenda(day)
        context = {
            "plan_for": day.isoformat(),
            "today_numbers": self.stats(self.today()),
            "today_call_reviews": [
                {"lead": f"{c['name']} ({c['company']})", "outcome": c["outcome"], **{k: c["review"].get(k) for k in ("summary", "went_well", "to_improve")}}
                for c in self.call_reviews(self.today())
            ],
            "meetings": [
                {"time": m["local_time"], "with": f"{m['name']}, {m['lead_title']} at {m['company']}", "research": m["research"][:600]}
                for m in agenda["meetings"]
            ],
            "tasks": [{"task": t["title"], "kind": t["kind"], "overdue": t["overdue"]} for t in agenda["tasks"]],
            "email_drafts_waiting_for_approval": agenda["pending_drafts"],
        }
        try:
            plan = self.ai.plan_day(context)
        except Exception as exc:  # the report must still go out if Claude is down
            log.warning("Planning failed: %s", exc)
            plan = ""
        return agenda, plan
