"""Slack message text and Block Kit layouts (Korean UI)."""

from __future__ import annotations

from typing import Any

STAGE_EMOJI = {
    "new": "🆕", "drafted": "📝", "contacted": "✉️", "engaged": "💬",
    "meeting_booked": "📅", "won": "🏆", "lost": "🪦",
}
STAGE_LABEL = {
    "new": "신규", "drafted": "초안 작성", "contacted": "연락함", "engaged": "대화 중",
    "meeting_booked": "미팅 예약", "won": "성사", "lost": "종료",
}
OUTCOME_LABEL = {
    "connected": "✅ 연결됨", "voicemail": "📼 음성메시지", "no_answer": "📵 부재중",
    "meeting_booked": "📅 미팅 확정", "not_interested": "🙅 관심 없음", "wrong_number": "❌ 잘못된 번호",
}
SENTIMENT_LABEL = {"positive": "긍정적", "neutral": "보통", "negative": "부정적"}
TASK_EMOJI = {"followup_email": "✉️", "email": "✉️", "call": "📞", "meeting": "📅", "reply": "💬"}
WEEKDAYS = "월화수목금토일"


def esc(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def clip(text: str, limit: int = 2900) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def stage(stage_key: str) -> str:
    return f"{STAGE_EMOJI.get(stage_key, '')} {STAGE_LABEL.get(stage_key, stage_key)}"


def when(dt: Any) -> str:
    """2026-11-03 (화) 14:00"""
    return f"{dt:%Y-%m-%d} ({WEEKDAYS[dt.weekday()]}) {dt:%H:%M}"


def lead_line(lead: dict[str, Any]) -> str:
    who = esc(lead["name"])
    role = " ".join(esc(p) for p in (lead.get("company"), lead.get("title")) if p)
    return f"*{who}*" + (f" — {role}" if role else "") + f"  `#{lead['id']}`"


def section(text: str) -> dict[str, Any]:
    return {"type": "section", "text": {"type": "mrkdwn", "text": clip(text)}}


def context(text: str) -> dict[str, Any]:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": clip(text, 1900)}]}


def email_preview(email: dict[str, Any]) -> str:
    body = "\n".join(f"> {esc(line)}" if line else ">" for line in email["body"].splitlines())
    return f"*제목:* {esc(email['subject'])}\n{body}"


def draft_blocks(lead: dict[str, Any], email: dict[str, Any], show_research: bool = True) -> list[dict[str, Any]]:
    kind = "첫 이메일" if email["step"] == 0 else f"팔로업 #{email['step']}"
    blocks: list[dict[str, Any]] = [
        section(f"✉️ *{kind} 초안* · {lead_line(lead)}\n받는 사람: {esc(lead['email']) or '_이메일 주소 없음_'}")
    ]
    if show_research and lead.get("research"):
        blocks.append(context("🔎 " + esc(lead["research"])))
    blocks.append(section(email_preview(email)))
    value = str(email["id"])
    blocks.append({
        "type": "actions",
        "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "보내기"}, "style": "primary", "action_id": "send_email", "value": value},
            {"type": "button", "text": {"type": "plain_text", "text": "수정 후 보내기"}, "action_id": "edit_email", "value": value},
            {"type": "button", "text": {"type": "plain_text", "text": "다시 쓰기"}, "action_id": "regenerate_email", "value": value},
            {"type": "button", "text": {"type": "plain_text", "text": "건너뛰기"}, "style": "danger", "action_id": "skip_email", "value": value},
        ],
    })
    return blocks


def sent_blocks(lead: dict[str, Any], email: dict[str, Any], who: str = "") -> list[dict[str, Any]]:
    by = f" (<@{who}>)" if who else ""
    return [section(f"✅ *발송 완료*{by} → {lead_line(lead)}"), section(email_preview(email))]


def skipped_blocks(lead: dict[str, Any], email: dict[str, Any]) -> list[dict[str, Any]]:
    return [context(f"⏭️ 건너뜀: “{esc(email['subject'])}” · {lead_line(lead)}")]


def edit_modal(email: dict[str, Any], metadata: str) -> dict[str, Any]:
    return {
        "type": "modal",
        "callback_id": "edit_email_submit",
        "private_metadata": metadata,
        "title": {"type": "plain_text", "text": "이메일 수정"},
        "submit": {"type": "plain_text", "text": "보내기"},
        "close": {"type": "plain_text", "text": "취소"},
        "blocks": [
            {
                "type": "input", "block_id": "subject",
                "label": {"type": "plain_text", "text": "제목"},
                "element": {"type": "plain_text_input", "action_id": "value", "initial_value": email["subject"]},
            },
            {
                "type": "input", "block_id": "body",
                "label": {"type": "plain_text", "text": "본문"},
                "element": {"type": "plain_text_input", "action_id": "value", "multiline": True, "initial_value": email["body"]},
            },
        ],
    }


def call_text(lead: dict[str, Any], review: Any, task: dict[str, Any] | None) -> str:
    lines = [
        f"📞 *통화 기록* — {lead_line(lead)}",
        f"{OUTCOME_LABEL.get(review.outcome, review.outcome)} · 반응: {SENTIMENT_LABEL.get(review.sentiment, review.sentiment)} · 단계: {stage(lead['stage'])}",
        f"*요약:* {esc(review.summary)}",
        f"*잘한 점:* {esc(review.went_well)}",
        f"*개선할 점:* {esc(review.to_improve)}",
    ]
    if task:
        lines.append(f"*다음 할 일:* {TASK_EMOJI.get(task['kind'], '•')} {esc(task['title'])} — {task['due_date']}까지 (할 일 `{task['id']}`)")
    return "\n".join(lines)


def meeting_text(lead: dict[str, Any], start: Any, minutes: int, invited: bool) -> str:
    invite = "캘린더 초대 메일 발송 ✅" if invited else "초대 메일은 안 보냄 (이메일 주소가 없거나 메일 설정 전)"
    return f"📅 *미팅 예약* — {lead_line(lead)}\n{when(start)} · {minutes}분 · {invite}"


def stats_text(title: str, s: dict[str, Any]) -> str:
    outcomes = ", ".join(f"{OUTCOME_LABEL.get(k, k)} {v}" for k, v in sorted(s["calls_by_outcome"].items())) or "—"
    return (
        f"*{title}*\n"
        f"• 🆕 신규 리드 *{s['new_leads']}*   ✉️ 보낸 메일 *{s['emails_sent']}*   💬 답장 *{s['replies']}*\n"
        f"• 📞 통화 *{s['calls']}*   ✅ 연결 *{s['connects']}* ({s['connect_rate']}%)\n"
        f"   {outcomes}\n"
        f"• 📅 잡은 미팅 *{s['meetings_booked']}*   🤝 진행한 미팅 *{s['meetings_held']}*"
    )


def agenda_text(agenda: dict[str, Any], heading: str) -> str:
    lines = [f"*{heading}* ({agenda['date']})"]
    if agenda["meetings"]:
        lines.append("*미팅*")
        lines += [f"• {m['local_time']} — {esc(m['name'])} ({esc(m['company'])}), {m['duration_min']}분" for m in agenda["meetings"]]
    if agenda["tasks"]:
        lines.append("*할 일*")
        for t in agenda["tasks"]:
            late = " ⚠️ _기한 지남_" if t["overdue"] else ""
            phone = f" · {esc(t['phone'])}" if t["kind"] == "call" and t.get("phone") else ""
            lines.append(f"• {TASK_EMOJI.get(t['kind'], '•')} {esc(t['title'])}{phone}{late}  `{t['id']}`")
    if agenda["pending_drafts"]:
        lines.append(f"• 📝 승인 기다리는 이메일 초안 {agenda['pending_drafts']}개")
    if len(lines) == 1:
        lines.append("아직 잡힌 일정이 없어요. `/lead`로 타깃을 추가해 보세요.")
    return "\n".join(lines)


def pipeline_text(pipeline: dict[str, list[dict[str, Any]]]) -> str:
    lines = ["*파이프라인*"]
    for stage_key, leads in pipeline.items():
        names = ", ".join(f"{esc(l['name'])} ({esc(l['company'])})" for l in leads[:6])
        more = f" 외 {len(leads) - 6}명" if len(leads) > 6 else ""
        lines.append(f"{stage(stage_key)} — {len(leads)}명" + (f": {names}{more}" if leads else ""))
    return "\n".join(lines)


def lead_detail_text(detail: dict[str, Any], local: Any) -> str:
    lead = detail["lead"]
    lines = [
        lead_line(lead),
        f"단계: {stage(lead['stage'])} · {esc(lead['email']) or '이메일 없음'} · {esc(lead['phone']) or '전화번호 없음'}",
    ]
    if lead["research"]:
        lines.append(f"_{esc(lead['research'][:600])}_")
    if detail["meetings"]:
        lines.append("*미팅:* " + ", ".join(when(local(m["starts_at"])) for m in detail["meetings"]))
    if detail["tasks"]:
        lines.append("*남은 할 일:*")
        lines += [f"• {esc(t['title'])} — {t['due_date']}까지 `{t['id']}`" for t in detail["tasks"]]
    if detail["activities"]:
        lines.append("*기록:*")
        for a in detail["activities"]:
            note = "" if a["type"] == "stage_change" else esc(a["summary"] or a["notes"])[:160]
            lines.append(f"• {local(a['created_at']):%m-%d %H:%M} {activity_label(a)} {note}".rstrip())
    return "\n".join(lines)


def activity_label(a: dict[str, Any]) -> str:
    if a["type"] == "call":
        return f"통화 {OUTCOME_LABEL.get(a['outcome'], a['outcome'])}"
    if a["type"] == "stage_change":
        return f"단계 → {stage(a['outcome'])}"
    return {"email_sent": "✉️ 메일 발송", "reply": "💬 답장", "meeting_booked": "📅 미팅 예약"}.get(a["type"], a["type"])
