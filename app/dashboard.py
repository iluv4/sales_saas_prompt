"""A small read-only CRM dashboard served with the standard library (no extra dependencies)."""

from __future__ import annotations

import html
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .formatting import OUTCOME_LABEL, WEEKDAYS, stage
from .service import SalesService

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#1d2433;--muted:#6b7385;--line:#e3e6ec;--accent:#3b5bdb;--warn:#c92a2a}
@media (prefers-color-scheme:dark){:root{--bg:#14171c;--card:#1d2128;--ink:#e8eaef;--muted:#9aa2b1;--line:#2c313a;--accent:#7c9cff;--warn:#ff8787}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 system-ui,-apple-system,"Apple SD Gothic Neo","Malgun Gothic","Segoe UI",sans-serif}
main{max-width:1180px;margin:0 auto;padding:24px 16px}h1{font-size:22px;margin:0 0 4px}h2{font-size:15px;margin:0 0 12px}
.muted{color:var(--muted)}.grid{display:grid;gap:12px}.tiles{grid-template-columns:repeat(auto-fit,minmax(150px,1fr));margin:18px 0}
.two{grid-template-columns:repeat(auto-fit,minmax(340px,1fr))}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;overflow-x:auto}
.tile b{display:block;font-size:26px;font-variant-numeric:tabular-nums}.tile span{color:var(--muted);font-size:12px}
.tile small{color:var(--muted);font-size:12px}table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:500;font-size:12px}.bar{display:flex;gap:6px;flex-wrap:wrap}.pill{border:1px solid var(--line);border-radius:999px;padding:4px 10px}
.warn{color:var(--warn)}ul{margin:0;padding-left:18px}
"""


def _e(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def render(service: SalesService) -> str:
    today = service.today()
    day = service.stats(today)
    week = service.stats(today - timedelta(days=6), 7)
    tomorrow = service.agenda(today + timedelta(days=1))
    pipeline = service.pipeline()

    def tile(label: str, key: str, suffix: str = "") -> str:
        return f'<div class="card tile"><span>{label}</span><b>{_e(day[key])}{suffix}</b><small>7일: {_e(week[key])}{suffix}</small></div>'

    tiles = "".join([
        tile("오늘 통화", "calls"), tile("연결", "connects"), tile("연결률", "connect_rate", "%"),
        tile("보낸 메일", "emails_sent"), tile("답장", "replies"), tile("잡은 미팅", "meetings_booked"),
        tile("진행한 미팅", "meetings_held"),
    ])
    stages = "".join(f'<span class="pill">{_e(stage(s))} <b>{len(l)}</b></span>' for s, l in pipeline.items())

    meetings = "".join(
        f"<li>{_e(m['local_time'])} — {_e(m['name'])} ({_e(m['company'])}), {_e(m['duration_min'])}분</li>" for m in tomorrow["meetings"]
    ) or '<li class="muted">미팅 없음</li>'
    tasks = "".join(
        f"<li{' class=warn' if t['overdue'] else ''}>{_e(t['title'])} <span class=muted>{_e(t['due_date'])}까지 · #{_e(t['id'])}</span></li>"
        for t in tomorrow["tasks"]
    ) or '<li class="muted">할 일 없음</li>'

    calls = "".join(
        f"<tr><td>{service.local(c['created_at']):%m-%d %H:%M}</td><td>{_e(c['name'])}<br><span class=muted>{_e(c['company'])}</span></td>"
        f"<td>{_e(OUTCOME_LABEL.get(c['outcome'], c['outcome']))}</td><td>{_e(c['summary'])}</td>"
        f"<td>{_e(c['review'].get('to_improve', ''))}</td></tr>"
        for c in reversed(service.call_reviews(today - timedelta(days=6), 7))
    ) or '<tr><td colspan=5 class="muted">이번 주 통화 기록이 없어요</td></tr>'

    leads = service.db.query("SELECT * FROM leads ORDER BY updated_at DESC LIMIT 50")
    lead_rows = "".join(
        f"<tr><td>#{l['id']}</td><td>{_e(l['name'])}</td><td>{_e(l['title'])}</td><td>{_e(l['company'])}</td>"
        f"<td>{_e(stage(l['stage']))}</td><td>{_e(l['email'])}</td>"
        f"<td>{service.local(l['updated_at']):%Y-%m-%d}</td></tr>"
        for l in leads
    ) or '<tr><td colspan=7 class="muted">아직 리드가 없어요. Slack에서 /lead로 추가하세요.</td></tr>'

    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>세일즈 에이전트 CRM</title>
<meta http-equiv="refresh" content="120"><style>{CSS}</style></head><body><main>
<h1>세일즈 에이전트 CRM</h1><div class="muted">{today:%Y년 %m월 %d일} ({WEEKDAYS[today.weekday()]})</div>
<div class="grid tiles">{tiles}</div>
<div class="card"><h2>파이프라인</h2><div class="bar">{stages}</div></div>
<div class="grid two" style="margin-top:12px">
<div class="card"><h2>내일 미팅 ({_e(tomorrow['date'])})</h2><ul>{meetings}</ul></div>
<div class="card"><h2>내일까지 할 일</h2><ul>{tasks}</ul>
<p class="muted">Slack에서 승인 기다리는 이메일 초안 {_e(tomorrow['pending_drafts'])}개</p></div>
</div>
<div class="card" style="margin-top:12px"><h2>이번 주 통화</h2><table><tr><th>시간</th><th>리드</th><th>결과</th><th>요약</th><th>개선할 점</th></tr>{calls}</table></div>
<div class="card" style="margin-top:12px"><h2>리드</h2><table><tr><th>번호</th><th>이름</th><th>직함</th><th>회사</th><th>단계</th><th>이메일</th><th>최근 변경</th></tr>{lead_rows}</table></div>
</main></body></html>"""


def serve(service: SalesService, host: str, port: int, token: str = "") -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            url = urlparse(self.path)
            if url.path != "/":
                self.send_error(404)
                return
            if token and parse_qs(url.query).get("token", [""])[0] != token:
                self.send_error(401, "Add ?token=<DASHBOARD_TOKEN> to the URL")
                return
            page = render(service).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)

        def log_message(self, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True, name="dashboard").start()
    return server
