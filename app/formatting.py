"""Slack message text and Block Kit layouts."""

from __future__ import annotations

from typing import Any

STAGE_EMOJI = {
    "new": "🆕", "drafted": "📝", "contacted": "✉️", "engaged": "💬",
    "meeting_booked": "📅", "won": "🏆", "lost": "🪦",
}
OUTCOME_LABEL = {
    "connected": "✅ Connected", "voicemail": "📼 Voicemail", "no_answer": "📵 No answer",
    "meeting_booked": "📅 Meeting booked", "not_interested": "🙅 Not interested", "wrong_number": "❌ Wrong number",
}
TASK_EMOJI = {"followup_email": "✉️", "email": "✉️", "call": "📞", "meeting": "📅", "reply": "💬"}


def esc(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def clip(text: str, limit: int = 2900) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def lead_line(lead: dict[str, Any]) -> str:
    who = esc(lead["name"])
    role = ", ".join(esc(p) for p in (lead.get("title"), lead.get("company")) if p)
    return f"*{who}*" + (f" — {role}" if role else "") + f"  `#{lead['id']}`"


def section(text: str) -> dict[str, Any]:
    return {"type": "section", "text": {"type": "mrkdwn", "text": clip(text)}}


def context(text: str) -> dict[str, Any]:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": clip(text, 1900)}]}


def email_preview(email: dict[str, Any]) -> str:
    body = "\n".join(f"> {esc(line)}" if line else ">" for line in email["body"].splitlines())
    return f"*Subject:* {esc(email['subject'])}\n{body}"


def draft_blocks(lead: dict[str, Any], email: dict[str, Any], show_research: bool = True) -> list[dict[str, Any]]:
    kind = "Intro email" if email["step"] == 0 else f"Follow-up #{email['step']}"
    blocks: list[dict[str, Any]] = [section(f"✉️ *{kind} draft* for {lead_line(lead)}\nTo: {esc(lead['email']) or '_no email address_'}")]
    if show_research and lead.get("research"):
        blocks.append(context("🔎 " + esc(lead["research"])))
    blocks.append(section(email_preview(email)))
    value = str(email["id"])
    blocks.append({
        "type": "actions",
        "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "Send"}, "style": "primary", "action_id": "send_email", "value": value},
            {"type": "button", "text": {"type": "plain_text", "text": "Edit & send"}, "action_id": "edit_email", "value": value},
            {"type": "button", "text": {"type": "plain_text", "text": "Rewrite"}, "action_id": "regenerate_email", "value": value},
            {"type": "button", "text": {"type": "plain_text", "text": "Skip"}, "style": "danger", "action_id": "skip_email", "value": value},
        ],
    })
    return blocks


def sent_blocks(lead: dict[str, Any], email: dict[str, Any], who: str = "") -> list[dict[str, Any]]:
    by = f" by <@{who}>" if who else ""
    return [section(f"✅ *Sent*{by} to {lead_line(lead)}"), section(email_preview(email))]


def skipped_blocks(lead: dict[str, Any], email: dict[str, Any]) -> list[dict[str, Any]]:
    return [context(f"⏭️ Skipped draft “{esc(email['subject'])}” for {lead_line(lead)}")]


def edit_modal(email: dict[str, Any], metadata: str) -> dict[str, Any]:
    return {
        "type": "modal",
        "callback_id": "edit_email_submit",
        "private_metadata": metadata,
        "title": {"type": "plain_text", "text": "Edit email"},
        "submit": {"type": "plain_text", "text": "Send"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "input", "block_id": "subject",
                "label": {"type": "plain_text", "text": "Subject"},
                "element": {"type": "plain_text_input", "action_id": "value", "initial_value": email["subject"]},
            },
            {
                "type": "input", "block_id": "body",
                "label": {"type": "plain_text", "text": "Body"},
                "element": {"type": "plain_text_input", "action_id": "value", "multiline": True, "initial_value": email["body"]},
            },
        ],
    }


def call_text(lead: dict[str, Any], review: Any, task: dict[str, Any] | None) -> str:
    outcome = OUTCOME_LABEL.get(review.outcome, review.outcome)
    lines = [
        f"📞 *Call logged* — {lead_line(lead)}",
        f"{outcome} · sentiment: {review.sentiment} · stage: {STAGE_EMOJI.get(lead['stage'], '')} {lead['stage']}",
        f"*Summary:* {esc(review.summary)}",
        f"*Went well:* {esc(review.went_well)}",
        f"*Improve:* {esc(review.to_improve)}",
    ]
    if task:
        lines.append(f"*Next:* {TASK_EMOJI.get(task['kind'], '•')} {esc(task['title'])} — due {task['due_date']} (task `{task['id']}`)")
    return "\n".join(lines)


def stats_text(title: str, s: dict[str, Any]) -> str:
    outcomes = ", ".join(f"{OUTCOME_LABEL.get(k, k)} {v}" for k, v in sorted(s["calls_by_outcome"].items())) or "—"
    return (
        f"*{title}*\n"
        f"• 🆕 New leads: *{s['new_leads']}*   ✉️ Emails sent: *{s['emails_sent']}*   💬 Replies: *{s['replies']}*\n"
        f"• 📞 Calls: *{s['calls']}*   ✅ Connects: *{s['connects']}* ({s['connect_rate']}%)\n"
        f"   {outcomes}\n"
        f"• 📅 Meetings booked: *{s['meetings_booked']}*   🤝 Meetings held: *{s['meetings_held']}*"
    )


def agenda_text(agenda: dict[str, Any], heading: str) -> str:
    lines = [f"*{heading}* ({agenda['date']})"]
    if agenda["meetings"]:
        lines.append("*Meetings*")
        lines += [f"• {m['local_time']} — {esc(m['name'])} ({esc(m['company'])}), {m['duration_min']} min" for m in agenda["meetings"]]
    if agenda["tasks"]:
        lines.append("*To do*")
        for t in agenda["tasks"]:
            late = " ⚠️ _overdue_" if t["overdue"] else ""
            phone = f" · {esc(t['phone'])}" if t["kind"] == "call" and t.get("phone") else ""
            lines.append(f"• {TASK_EMOJI.get(t['kind'], '•')} {esc(t['title'])}{phone}{late}  `{t['id']}`")
    if agenda["pending_drafts"]:
        lines.append(f"• 📝 {agenda['pending_drafts']} email draft(s) waiting for your approval")
    if len(lines) == 1:
        lines.append("Nothing scheduled yet. Add targets with `/lead`.")
    return "\n".join(lines)


def pipeline_text(pipeline: dict[str, list[dict[str, Any]]]) -> str:
    lines = ["*Pipeline*"]
    for stage, leads in pipeline.items():
        names = ", ".join(f"{esc(l['name'])} ({esc(l['company'])})" for l in leads[:6])
        more = f" +{len(leads) - 6} more" if len(leads) > 6 else ""
        lines.append(f"{STAGE_EMOJI[stage]} *{stage}* — {len(leads)}" + (f": {names}{more}" if leads else ""))
    return "\n".join(lines)


def lead_detail_text(detail: dict[str, Any], local: Any) -> str:
    lead = detail["lead"]
    lines = [
        lead_line(lead),
        f"Stage: {STAGE_EMOJI.get(lead['stage'], '')} {lead['stage']} · {esc(lead['email']) or 'no email'} · {esc(lead['phone']) or 'no phone'}",
    ]
    if lead["research"]:
        lines.append(f"_{esc(lead['research'][:600])}_")
    if detail["meetings"]:
        lines.append("*Meetings:* " + ", ".join(f"{local(m['starts_at']):%Y-%m-%d %H:%M}" for m in detail["meetings"]))
    if detail["tasks"]:
        lines.append("*Open tasks:*")
        lines += [f"• {esc(t['title'])} — due {t['due_date']} `{t['id']}`" for t in detail["tasks"]]
    if detail["activities"]:
        lines.append("*Timeline:*")
        for a in detail["activities"]:
            label = OUTCOME_LABEL.get(a["outcome"], a["outcome"]) if a["type"] == "call" else a["outcome"]
            lines.append(f"• {local(a['created_at']):%m-%d %H:%M} {a['type']} {label} {esc(a['summary'] or a['notes'])[:160]}")
    return "\n".join(lines)
