# Claude Litellm Proxy

ระบบ proxy + telemetry collector สำหรับ monitor การใช้งาน Claude Code ของพนักงาน ดัก prompt, token usage, และ cost โดยไม่ต้องแก้ไข Claude Code ฝั่งพนักงาน พร้อมส่งข้อมูลขึ้น Cloudflare D1 ผ่าน Worker API แบบ real-time

---

## สถาปัตยกรรมระบบ

```
┌──────────────────────────────────────────────────────────────────┐
│                        เครื่องพนักงาน                             │
│                                                                  │
│   Claude Code CLI                                                │
│       │                                                          │
│       ├─── API requests ────────────────────────────────────────►│
│       │    ANTHROPIC_BASE_URL=http://<server>:8080               │
│       │                                                          │
│       └─── OTel telemetry ──────────────────────────────────────►│
│            OTEL_EXPORTER_OTLP_ENDPOINT=http://<server>:4318      │
└──────────────────────────────────────────────────────────────────┘
                          │                       │
              ┌───────────┘                       └──────────────┐
              ▼                                                  ▼
┌──────────────────────────────┐       ┌────────────────────────────────┐
│  proxy  (port 8080)          │       │  otel-collector (port 4317/18) │
│                              │       │                                │
│  1. รับ request              │       │  รับ metrics + logs            │
│  2. POST /api/prompt ───────►│──────►│  CF Worker                     │
│  3. forward → Anthropic      │       │  POST /ingest/otel ────────────►
│  4. stream response กลับ    │       │                                │
│  5. POST /api/usage ────────►│──────►│  CF Worker                     │
│  6. เขียน proxy-logs.jsonl   │       │  เขียน otel-logs.jsonl         │
└──────────────────────────────┘       └────────────────────────────────┘
         │              │                              │
         ▼              ▼                              ▼
  api.anthropic   Cloudflare D1              Cloudflare D1
                  (prompt_logs)              (via /ingest/otel)
                  (usage_logs)
```

### Data Flow สำหรับ 1 request

```
1. พนักงานพิมพ์ prompt ใน Claude Code
2. Claude Code ส่ง POST /v1/messages → proxy:8080
3. proxy generate session_id (UUID) สำหรับ request นี้
4. proxy ส่ง POST /api/prompt ไป CF Worker (fire-and-forget)
5. proxy forward request ไป api.anthropic.com
6. Anthropic ส่ง streaming SSE response กลับ
7. proxy ส่งต่อ stream ให้ Claude Code แบบ real-time
8. หลัง stream จบ → parse SSE → ได้ tokens ครบ
9. proxy เขียน proxy-logs.jsonl (local backup)
10. proxy ส่ง POST /api/usage ไป CF Worker พร้อม token ทุกประเภท
11. Claude Code ส่ง OTel telemetry → otel-collector:4318
12. otel-collector เขียน otel-logs.jsonl + ส่ง POST /ingest/otel ไป CF Worker
```

---

## Cloudflare Worker

| Item | Value |
|------|-------|
| **Worker URL** | `https://claude-prompt-logger.cloudflare-training3.workers.dev` |
| **Dashboard** | `GET /` |
| **Auth** | Header `X-Api-Key: <key>` |
| **D1 Database** | `prompt-logger` |

### Endpoints ที่ระบบนี้ใช้

| Endpoint | ส่งจาก | ข้อมูล |
|----------|--------|--------|
| `POST /api/prompt` | proxy.py | session_id, cwd, char_count, approx_tokens, prompt |
| `POST /api/usage` | proxy.py | session_id, model, input/output/cache tokens, total_tokens |
| `POST /ingest/otel` | otel-collector | OTLP JSON — events + metrics จาก Claude Code |

---

## Components

### 1. proxy (port 8080)

**ไฟล์:** [proxy.py](proxy.py), [Dockerfile.proxy](Dockerfile.proxy)

| หน้าที่ | รายละเอียด |
|---------|-----------|
| รับ request ทุก HTTP method | GET, POST, PUT, DELETE, PATCH |
| กรอง hop-by-hop headers | ป้องกัน `host`, `connection`, `transfer-encoding`, `accept-encoding` |
| Streaming support | `httpx.AsyncClient` + `StreamingResponse` ส่ง SSE chunk-by-chunk |
| Parse SSE | แยก `message_start` → input/cache tokens, `message_delta` → output tokens |
| ส่ง prompt log | `POST /api/prompt` ทันทีที่รับ request (ก่อน Anthropic ตอบ) |
| ส่ง usage log | `POST /api/usage` หลัง stream จบ พร้อม token ครบทุกประเภท |
| Local backup | เขียน `proxy-logs.jsonl` ทุก request |

**สิ่งที่บันทึกลง proxy-logs.jsonl:**

```json
{
  "timestamp": "2026-04-28T10:29:08.391000+00:00",
  "model": "claude-sonnet-4-6",
  "input_tokens": 3,
  "output_tokens": 102,
  "cache_creation_input_tokens": 572,
  "cache_read_input_tokens": 122560,
  "prompt": "[user] prompt ของพนักงาน\n[assistant] ...",
  "response": "คำตอบจาก Claude"
}
```

### 2. otel-collector (port 4317/4318)

**ไฟล์:** [otel-config.yaml](otel-config.yaml)

OpenTelemetry Collector รับ telemetry จาก Claude Code CLI โดยตรง ส่งออก 2 ที่พร้อมกัน:

| Exporter | ปลายทาง | ข้อมูล |
|----------|---------|--------|
| `file` | `logs/otel-logs.jsonl` | local backup |
| `otlphttp/worker` | CF Worker `/ingest/otel` | ส่งขึ้น D1 |

**Events ที่ Claude Code ส่งมา:**
- `claude_code.user_prompt` — ทุกครั้งที่พนักงานส่ง prompt
- `claude_code.api_request` — ผล API call: model, tokens, cost_usd, duration_ms
- `claude_code.tool_decision` — approve/deny tool (Bash, Edit, etc.)
- `claude_code.tool_result` — ผล tool execution
- `claude_code.cost.usage` / `claude_code.token.usage` — metrics สะสม

---

## ไฟล์ในโปรเจกต์

| ไฟล์ | หน้าที่ |
|------|---------|
| [proxy.py](proxy.py) | FastAPI proxy + ส่ง log ไป CF Worker |
| [Dockerfile.proxy](Dockerfile.proxy) | build Docker image สำหรับ proxy |
| [docker-compose.yml](docker-compose.yml) | รัน proxy + otel-collector |
| [otel-config.yaml](otel-config.yaml) | config OTel Collector (file + worker exporter) |
| [.env](.env) | `CF_WORKER_URL` + `CF_WORKER_SECRET` (ไม่ commit) |
| [.env.example](.env.example) | template สำหรับ `.env` |
| [requirements.txt](requirements.txt) | Python dependencies |
| [employee_settings.json](employee_settings.json) | template env vars สำหรับพนักงาน |
| [INTEGRATION.md](INTEGRATION.md) | คู่มือ CF Worker API ฉบับเต็ม |
| [DOCKER.md](DOCKER.md) | คู่มือ Docker commands |
| [logs/proxy-logs.jsonl](logs/proxy-logs.jsonl) | local backup: prompt + token usage |
| [logs/otel-logs.jsonl](logs/otel-logs.jsonl) | local backup: OTel telemetry |

---

## การติดตั้ง (Admin Setup)

### ความต้องการ

- Docker Desktop (หรือ Docker Engine + Compose)
- Port 8080, 4317, 4318 ต้องเปิด firewall ให้เครื่องพนักงานเข้าถึงได้
- CF Worker secret จาก Cloudflare Dashboard

### 1. ตั้งค่า .env

```bash
cp .env.example .env
# แก้ไข CF_WORKER_SECRET ให้เป็น key จริง
```

```env
CF_WORKER_URL=https://claude-prompt-logger.cloudflare-training3.workers.dev
CF_WORKER_SECRET=your-secret-here
```

### 2. Build และรัน

```bash
docker compose up --build -d
```

### 3. ตรวจสอบ

```bash
docker compose ps
docker compose logs proxy --tail=20
# ต้องเห็น: POST /api/prompt → 200 OK
#           POST /api/usage  → 200 OK
```

### 4. ทดสอบ proxy

```bash
curl -X POST http://localhost:8080/v1/messages \
  -H "Content-Type: application/json" \
  -H "x-api-key: <ANTHROPIC_KEY>" \
  -H "anthropic-version: 2023-06-01" \
  -d '{"model":"claude-haiku-4-5-20251001","max_tokens":10,"messages":[{"role":"user","content":"hi"}]}'
```

---

## การตั้งค่าฝั่งพนักงาน

Copy จาก [employee_settings.json](employee_settings.json) ไปใส่ใน Claude Code settings:

- **Windows:** `%APPDATA%\Claude\settings.json`
- **Mac/Linux:** `~/.claude/settings.json`

```json
{
  "env": {
    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
    "OTEL_METRICS_EXPORTER": "otlp",
    "OTEL_LOGS_EXPORTER": "otlp",
    "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
    "OTEL_EXPORTER_OTLP_ENDPOINT": "http://<SERVER_IP>:4318",
    "OTEL_METRIC_EXPORT_INTERVAL": "10000",
    "OTEL_LOGS_EXPORT_INTERVAL": "5000",
    "OTEL_RESOURCE_ATTRIBUTES": "user.email=<EMAIL>,department=<DEPT>,service.name=claude-code",
    "ANTHROPIC_BASE_URL": "http://<SERVER_IP>:8080"
  }
}
```

หลังแก้ไขแล้ว **restart Claude Code** เพื่อให้ค่า env มีผล

---

## การดู Log

### Dashboard (real-time)

```
https://claude-prompt-logger.cloudflare-training3.workers.dev
```

auto-refresh ทุก 60 วินาที แสดง Total Prompts, Sessions, Token breakdown, Model breakdown, Recent Activity

### Docker logs

```bash
docker compose logs -f proxy
# ดูสถานะการส่งข้อมูลไป CF Worker แบบ real-time
```

### Local backup files

```bash
# ดู proxy log real-time
tail -f logs/proxy-logs.jsonl

# สรุป token + cache usage ต่อ model
python3 -c "
import sys, json
from collections import defaultdict
totals = defaultdict(lambda: {'in':0,'out':0,'cache_c':0,'cache_r':0,'calls':0})
for line in open('logs/proxy-logs.jsonl'):
    e = json.loads(line.strip())
    m = e.get('model','?')
    totals[m]['in']      += e.get('input_tokens') or 0
    totals[m]['out']     += e.get('output_tokens') or 0
    totals[m]['cache_c'] += e.get('cache_creation_input_tokens') or 0
    totals[m]['cache_r'] += e.get('cache_read_input_tokens') or 0
    totals[m]['calls']   += 1
for m, v in totals.items():
    print(f'{m}: {v[\"calls\"]} calls | input={v[\"in\"]} output={v[\"out\"]} cache_create={v[\"cache_c\"]} cache_read={v[\"cache_r\"]}')
"
```

---

## Troubleshooting

### CF Worker ได้ 401

`CF_WORKER_SECRET` ใน `.env` ผิดหรือยังไม่ได้ set
```bash
# แก้ .env แล้ว restart (ไม่ต้อง rebuild)
docker compose up -d
```

### otel-logs.jsonl หายหลังลบ

```bash
# truncate แทนการลบ
> logs/otel-logs.jsonl
# หรือถ้าลบไปแล้ว ให้ restart
docker compose restart otel-collector
```

### proxy-logs.jsonl ไม่มีข้อมูล

1. ตรวจ `ANTHROPIC_BASE_URL` ชี้มาที่ proxy ถูกต้อง
2. ตรวจ port 8080 firewall
3. `docker compose logs proxy`

---

## หยุดและ Rebuild

```bash
# rebuild หลังแก้ proxy.py
docker compose up --build -d

# restart อย่างเดียว (แก้ .env หรือ otel-config.yaml)
docker compose up -d

# หยุดทั้งหมด
docker compose down
```
