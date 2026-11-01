# 연결하고 배포하기

이 문서대로 하면 30분 정도면 Slack에서 실제로 쓸 수 있습니다. 순서는 이렇습니다.

1. 키와 토큰 준비하기: Claude API 키, Slack 앱, Gmail 앱 비밀번호
2. 내 컴퓨터에서 먼저 실행해 보기 (선택, 추천)
3. Railway에 배포해서 24시간 돌리기

> **토큰과 비밀번호는 채팅이나 코드에 붙여넣지 마세요.** 내 컴퓨터의 `.env` 파일이나 Railway의
> Variables 화면에만 넣습니다. `.env`는 `.gitignore`에 들어 있어서 GitHub에 올라가지 않습니다.

---

## 1. 키와 토큰 준비하기

### 1-1. Claude API 키

1. https://console.anthropic.com 에 로그인합니다.
2. **API Keys → Create Key**를 눌러 키를 만듭니다. `sk-ant-`로 시작합니다.
3. **Billing**에서 결제 수단을 등록합니다.

→ `ANTHROPIC_API_KEY`

### 1-2. Slack 앱 (토큰 2개와 채널 ID 1개)

1. https://api.slack.com/apps 에서 **Create New App → From a manifest**를 누릅니다.
2. 앱을 설치할 워크스페이스를 고르고, 저장소의 `slack_manifest.yml` 내용을 통째로 붙여넣은 뒤 **Create**를 누릅니다.
   `/lead`, `/call`, `/meeting`, `/sales` 명령어와 권한이 자동으로 설정됩니다.
3. **Basic Information → App-Level Tokens → Generate Token and Scopes**에서 이름을 아무거나 쓰고,
   scope에 `connections:write`를 추가해 만듭니다. `xapp-`로 시작합니다.
   → `SLACK_APP_TOKEN`
4. **Install App → Install to Workspace → 허용**을 누르면 **Bot User OAuth Token**이 나옵니다. `xoxb-`로 시작합니다.
   → `SLACK_BOT_TOKEN`
5. Slack에서 `#sales` 같은 채널을 만들고 `/invite @Sales Agent`로 봇을 초대합니다.
   채널 이름을 누르면 나오는 창 맨 아래에 `C`로 시작하는 **채널 ID**가 있습니다.
   → `SLACK_CHANNEL_ID` (매일 리포트, 답장 알림, 팔로업 초안이 이 채널로 옵니다)

Socket Mode를 쓰기 때문에 서버 주소(URL)를 Slack에 등록할 필요가 없습니다.

### 1-3. Gmail (발송 + 답장 확인)

Gmail과 Google Workspace 기준입니다. 다른 메일이면 그 서비스의 SMTP/IMAP 주소와 비밀번호를 넣으면 됩니다.

1. Google 계정 → **보안**에서 **2단계 인증**을 켭니다. 앱 비밀번호는 2단계 인증이 켜져 있어야 만들 수 있습니다.
2. https://myaccount.google.com/apppasswords 에서 이름(예: `sales-agent`)을 넣고 만들면 16자리 비밀번호가 나옵니다.
   공백 없이 붙여서 씁니다.
   → `SMTP_PASSWORD`
3. Gmail 설정의 **전달 및 POP/IMAP**에서 IMAP이 꺼져 있으면 켭니다.
4. 나머지 값은 이렇게 넣습니다.

```
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=내메일@회사.com
SENDER_EMAIL=내메일@회사.com
IMAP_HOST=imap.gmail.com
```

회사 Google Workspace 계정인데 앱 비밀번호 메뉴가 안 보이면, 관리자가 막아 둔 것이니 관리자에게 요청하세요.

### 1-4. 우리 회사 소개

`SENDER_NAME`, `SENDER_TITLE`, `SENDER_COMPANY`, `VALUE_PROP`, `BOOKING_LINK`를 채웁니다.
특히 `VALUE_PROP`(무엇을 누구에게 얼마나 좋게 해 주는지)이 구체적일수록 이메일이 좋아집니다.
예시는 `.env.example`에 있습니다.

---

## 2. 내 컴퓨터에서 먼저 실행해 보기 (추천)

Python 3.11 이상이 필요합니다.

```bash
git clone https://github.com/iluv4/sales_saas_prompt.git
```

```bash
cd sales_saas_prompt
```

```bash
pip install -r requirements.txt
```

```bash
cp .env.example .env
```

`.env`에 1번에서 받은 값을 넣고 실행합니다.

```bash
python main.py
```

Slack에서 `/sales 도움말`을 입력해 답이 오면 연결 성공입니다. 처음에는 **내 다른 메일 주소**를 타깃으로
`/lead`를 해서 초안과 발송을 시험해 보세요. 대시보드는 http://127.0.0.1:8080 에서 볼 수 있습니다.

컴퓨터를 끄면 에이전트도 멈추기 때문에(오전 9시 팔로업, 저녁 6시 리포트 포함), 계속 쓰려면 3번처럼 서버에 올립니다.

---

## 3. Railway에 배포하기 (24시간 실행)

Railway는 GitHub 저장소를 연결하면 `Dockerfile`을 보고 알아서 빌드해 주는 서비스입니다.
비용은 사용량에 따라 다르지만 이 정도 앱은 보통 월 몇 달러 수준입니다 (Hobby 요금제). 최신 요금은 railway.com에서 확인하세요.

1. https://railway.com 에 GitHub 계정으로 가입합니다.
2. **New Project → Deploy from GitHub repo**에서 `iluv4/sales_saas_prompt`를 고릅니다.
   - 배포할 브랜치는 서비스의 **Settings → Source**에서 고릅니다. 지금 코드는
     `claude/wizardly-cannon-7fj5u8` 브랜치에 있으니, 이 브랜치를 고르거나 먼저 `main`으로 합칩니다.
3. 서비스의 **Variables** 탭에서 **Raw Editor**를 열고, `.env`에 넣었던 내용을 그대로 붙여넣습니다.
   추가로 아래 두 줄을 넣습니다.
   ```
   DB_PATH=/data/sales.db
   DASHBOARD_TOKEN=아무도-못-맞출-긴-비밀번호
   ```
4. **Volume을 붙입니다.** CRM 데이터(SQLite 파일)가 재배포 때 지워지지 않게 하려면 꼭 필요합니다.
   프로젝트 화면에서 Volume을 새로 만들어 이 서비스에 연결하고, **Mount path**를 `/data`로 정합니다.
5. 배포가 끝나면 **Deployments → View logs**에서 에러가 없는지 봅니다. `대시보드: http://0.0.0.0:…`
   같은 줄이 보이면 실행 중입니다.
6. 대시보드를 밖에서 보려면 **Settings → Networking → Generate Domain**으로 주소를 만들고,
   `https://<주소>/?token=<DASHBOARD_TOKEN>`으로 접속합니다. `DASHBOARD_TOKEN`이 없으면 대시보드는 안전을 위해 켜지지 않습니다.

이후에는 GitHub에 코드를 올릴 때마다 Railway가 자동으로 다시 배포합니다.

> 로컬과 Railway를 **동시에** 켜 두지 마세요. 같은 Slack 토큰으로 두 곳이 연결되면 명령이 두 번 처리될 수 있습니다.

### Railway 말고 다른 곳에 올리고 싶다면

`Dockerfile`이 있으므로 Docker를 돌릴 수 있는 곳이면 어디든 됩니다 (Render, Fly.io, AWS Lightsail, 집에 있는 PC 등).
조건은 세 가지입니다. 프로세스가 **항상 켜져 있을 것**, `/data`가 **지워지지 않는 디스크**일 것, 위의 환경 변수를 넣을 것.

---

## 4. 잘 되는지 확인하기

| 확인할 것 | 방법 |
|---|---|
| Slack 연결 | `/sales 도움말` 입력 → 명령어 목록이 나오면 성공 |
| Claude 연결 | `/lead`로 내 다른 메일 주소를 타깃으로 추가 → 1~2분 안에 초안이 올라오면 성공 |
| 메일 발송 | 초안에서 **보내기** → 내 다른 메일함에 도착하면 성공 |
| 답장 감지 | 그 메일에 답장 → 15분 안에 Slack에 “답장 도착”이 오면 성공 |
| 리포트 | `/sales 리포트` → 오늘 숫자와 내일 계획이 나오면 성공 |

### 자주 생기는 문제

- **`/lead`를 쳤는데 "dispatch_failed"가 나와요**: 에이전트가 꺼져 있습니다. 로그를 확인하세요.
- **초안이 안 올라와요**: 봇이 그 채널에 없을 수 있습니다. `/invite @Sales Agent`를 하거나 `SLACK_CHANNEL_ID` 채널을 확인하세요.
- **보내기를 누르면 "SMTP" 오류가 나요**: 앱 비밀번호를 다시 확인하세요 (공백 없이 16자리, 2단계 인증 필수).
- **대시보드가 안 켜져요**: 서버에서는 `DASHBOARD_TOKEN`이 있어야 켜집니다.

### 보내기 전에 지킬 것

- 처음 2주는 하루 10~20통으로 시작해 천천히 늘리세요. 새 메일 계정에서 갑자기 많이 보내면 스팸으로 분류됩니다.
- 회사 도메인이라면 SPF, DKIM, DMARC 설정이 되어 있는지 IT 담당자에게 확인하세요.
- 한국의 정보통신망법은 영리 목적 광고성 메일에 사전 동의나 “(광고)” 표시를 요구할 수 있습니다. 대량 발송 전에 꼭 확인하세요.
