# Sales Agent

An AI sales assistant that runs in Slack. You give it target executives (for example CEOs of e-commerce
companies). For each person it:

1. **Researches them**: Claude searches the web for the person and their company.
2. **Writes a personal email** built on what it found, then posts the draft in Slack.
   Nothing is sent until you click **Send** (or **Edit & send**).
3. **Follows up**: if there's no reply, it drafts follow-ups on day 3 and day 7, sent in the
   same email thread. After the last email it creates a call task.
4. **Notices replies**: it checks your inbox, stops the follow-ups and tells you to book the meeting.
5. **Books meetings**: `/meeting` emails the prospect a real calendar invite (.ics).
6. **Tracks calls**: `/call` logs the outcome, and Claude summarizes the call, coaches you on it
   and creates the next task.
7. **Reports every evening**: today's numbers (calls, connects, emails, replies, meetings) plus
   tomorrow's meetings, tasks and a prioritized plan.

A built-in CRM (SQLite) stores everything, and a web dashboard shows it.

```
Slack ──/lead──▶ parse ─▶ research (web) ─▶ draft ─▶ [Send] ─▶ SMTP ─▶ follow-up tasks
      ──/call──▶ AI call review ─▶ stage + next task          IMAP ─▶ reply ─▶ stop sequence
      ──/meeting▶ meeting + .ics invite                        18:00 ─▶ daily report + plan
```

## Slack commands

| Command | What it does |
|---|---|
| `/lead Jane Kim, CEO of Acme Commerce, jane@acmecommerce.com` | Add one or more targets in any format (lists, CSV rows, LinkedIn snippets). Each gets a research brief and an email draft with **Send / Edit & send / Rewrite / Skip** buttons. |
| `/call Jane Kim connected wants pricing, call back Tuesday` | Log a call. The outcome word is optional (`connected`, `vm`, `na`, `meeting`, `ni`); Claude infers it from the notes otherwise. You get a summary, what went well, one thing to improve, and the next task. |
| `/meeting Jane Kim 2026-11-03 14:00 45` | Book a meeting (`tomorrow 10:30` works too). Emails a calendar invite and moves the lead to `meeting_booked`. |
| `/sales today` · `/sales week` | Calls, connects and connect rate, emails sent, replies, meetings booked and held. |
| `/sales tomorrow` | Tomorrow's meetings, tasks (overdue ones flagged), drafts waiting, and Claude's plan. |
| `/sales report` | Posts the full daily report now. |
| `/sales pipeline` · `/sales lead Jane Kim` | Leads by stage, or one lead's timeline. |
| `/sales done 12` · `/sales stage Jane Kim won` | Close a task, or move a lead to another stage. |
| `/sales email Jane Kim jane@acme.com` · `/sales phone …` | Fix contact details. |
| `/sales followups` | Draft any follow-ups that are due right now. |

To name a lead, use `#12`, the full name, the company or the email. If a name is ambiguous, write `Name | notes`.

Pipeline stages: `new → drafted → contacted → engaged → meeting_booked → won / lost`.

## Setup (about 15 minutes)

```bash
pip install -r requirements.txt
cp .env.example .env   # then fill it in (see below)
python main.py
```

1. **Claude**: put an API key from console.anthropic.com in `ANTHROPIC_API_KEY`.
2. **Slack**: at api.slack.com/apps, choose **Create New App → From a manifest** and paste
   `slack_manifest.yml`. Install the app to your workspace.
   - **OAuth & Permissions** gives you the bot token (`xoxb-…`): set it as `SLACK_BOT_TOKEN`.
   - **Basic Information → App-Level Tokens**: create one with the `connections:write` scope
     (`xapp-…`) and set it as `SLACK_APP_TOKEN`.
   - Make a channel such as `#sales`, invite the bot, and put its ID in `SLACK_CHANNEL_ID`.
     Daily reports, reply alerts and follow-up drafts are posted there.
3. **Email**: with Gmail or Google Workspace, turn on 2-Step Verification, create an
   [App Password](https://myaccount.google.com/apppasswords) and use it as `SMTP_PASSWORD`. The
   SMTP settings send email; `IMAP_HOST` lets the agent detect replies.
4. **Your pitch**: `SENDER_*`, `VALUE_PROP` and `BOOKING_LINK` are what Claude builds every
   email around. Spend 5 minutes on a sharp, specific `VALUE_PROP`.
5. Set `TIMEZONE` (for example `Asia/Seoul`) and `EMAIL_LANGUAGE` (for example `Korean`).

The dashboard runs at `http://127.0.0.1:8080`. If you expose it beyond localhost, set `DASHBOARD_TOKEN`
and open `/?token=…`.

Socket Mode means you don't need a public URL. It runs on a laptop, a small VM or any always-on box.
The scheduler (follow-ups at 09:00, reply checks every 15 minutes, report at 18:00) only runs while
the process is running.

## Costs and models

- Every Claude call uses `claude-opus-5-5`; you can change it with `CLAUDE_MODEL`. Calls are cheap
  except web research: each `/lead` person does up to 6 web searches and 4 page fetches. To save
  money, set `RESEARCH_MODE=site` so only the company homepage is read.
- Requests opt into Anthropic's server-side fallback (`fallbacks: "default"`). If a safety
  classifier declines a request, it is retried on the recommended fallback model rather than failing.

## Good practice, and staying legal

- **A human approves every email.** The agent never sends on its own. Keep it that way: it is
  what keeps quality high and complaints low.
- Every email ends with an opt-out line and a `List-Unsubscribe` header. Respect opt-outs: move
  those leads to `lost`.
- Cold email laws differ by country: CAN-SPAM in the US, GDPR/PECR in Europe, and the Information
  and Communications Network Act in Korea, which can require prior consent and an "(광고)" label
  on commercial email. Check what applies to your recipients before you send at volume.
- `DAILY_SEND_LIMIT` (40 by default) protects your domain's reputation. Use a domain with
  SPF, DKIM and DMARC set up, and warm up new mailboxes slowly.
- Claude is told never to invent facts, but **read the research and the draft before you click
  Send**. Web results can be about the wrong person with the same name.

## Project layout

```
main.py              start Slack (Socket Mode) + scheduler + dashboard
app/service.py       the sales workflow: leads, cadence, calls, meetings, stats (no Slack code)
app/ai.py            Claude prompts and structured outputs
app/slack_app.py     slash commands, approval buttons, edit modal, scheduled jobs
app/mailer.py        SMTP send, IMAP reply detection, calendar invites
app/db.py            SQLite schema (leads, emails, activities, meetings, tasks)
app/dashboard.py     read-only web dashboard
tests/               run with: python -m pytest
```

## Ideas for next steps

- Sync with HubSpot/Pipedrive instead of (or alongside) SQLite.
- Use Google Calendar's API to suggest free slots and create the event directly.
- Pull prospect lists from Apollo or LinkedIn Sales Navigator exports.
- Add call recording and transcription (for example Gong or a dialer) so `/call` notes fill in automatically.
- Add login and multiple workspaces to turn this into a multi-user SaaS.
