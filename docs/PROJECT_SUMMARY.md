# Claude Litellm Proxy — สรุปโปรเจค

## โปรเจคนี้คืออะไร

ระบบ **proxy สำหรับ monitor การใช้งาน Claude Code ของพนักงาน** โดยดัก prompt + token usage แบบ real-time  
พนักงานไม่ต้องแก้อะไรนอกจากตั้ง `ANTHROPIC_BASE_URL` ชี้มาที่ server นี้

---

## สถาปัตยกรรม

```
Claude Code (เครื่องพนักงาน)
    │ POST /v1/messages  ← ANTHROPIC_BASE_URL=http://<server>:8080
    ▼
proxy:8080  ← รันใน Docker บน server
    │
    ├── forward ไป api.anthropic.com  (ส่ง request จริง)
    ├── parse SSE stream → ดัก tokens
    ├── เขียน logs/proxy-logs.jsonl  (local backup)
    └── POST ไป Cloudflare Worker → เก็บลง D1 database
```

### Data Flow ต่อ 1 Prompt

```
1. Claude Code → proxy:8080  POST /v1/messages
2. proxy สร้าง session_id (UUID4)
3. proxy ส่ง prompt ไป CF Worker  POST /api/prompt  (fire-and-forget)
4. proxy forward request ไป api.anthropic.com
5. Anthropic ส่ง SSE streaming response กลับมา
6. proxy stream ต่อให้ Claude Code ทันที
7. proxy parse SSE events → ได้ tokens ครบ
8. proxy เขียน logs/proxy-logs.jsonl (local backup)
9. proxy ส่ง usage ไป CF Worker  POST /api/usage
```

---

## ไฟล์สำคัญ

| ไฟล์ | หน้าที่ |
|------|---------|
| `proxy.py` | หัวใจ — FastAPI app รับ request, forward, parse SSE, เขียน log, ส่งไป CF Worker |
| `Dockerfile.proxy` | Build Python 3.12 slim image |
| `docker-compose.yml` | รัน 2 service: proxy (8080) + otel-collector (4317/4318) |
| `otel-config.yaml` | Config OpenTelemetry Collector |
| `.env` | Secrets: CF_WORKER_SECRET, CF_WORKER_URL |
| `.env.example` | Template สำหรับ .env |
| `employee_settings.json` | ตัวอย่าง Claude Code settings ให้พนักงาน |
| `requirements.txt` | fastapi, httpx, uvicorn, python-dotenv |
| `logs/proxy-logs.jsonl` | Local backup log (Docker volume mount) |
| `setup-employee.ps1` | PowerShell script setup เครื่องพนักงาน |

---

## วิธีรัน

```bash
cp .env.example .env        # แก้ CF_WORKER_SECRET
docker compose up --build -d
```

## ฝั่งพนักงาน ตั้งใน Claude Code settings

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://<server_ip>:8080"
  }
}
```

---

## ข้อมูล Log — แต่ละ Field มาจากไหน

### Format (proxy-logs.jsonl)

```json
{
  "timestamp": "2026-04-28T10:29:08.391000+00:00",
  "account": "sk-ant-ap...3f4a",
  "account_email": "user@example.com",
  "ip_address": "192.168.1.10",
  "source": "Claude-Code/1.0",
  "model": "claude-sonnet-4-6",
  "status_code": 200,
  "input_tokens": 433,
  "output_tokens": 13,
  "cache_creation_input_tokens": 0,
  "cache_read_input_tokens": 0,
  "prompt": "สวัสดีครับ คุณคือใคร?",
  "response": "สวัสดีครับ ฉันคือ Claude..."
}
```

### ที่มาของแต่ละ Field

| Field | มาจากไหน | ฟังก์ชัน / บรรทัด |
|-------|----------|-------------------|
| `timestamp` | สร้างขึ้นตอน write_log — เวลาปัจจุบัน UTC | `datetime.now(timezone.utc).isoformat()` |
| `account` | HTTP request header `x-api-key` หรือ `Authorization: Bearer ...` ที่ Claude Code ส่งมา — **masked** เหลือแค่ 8 ตัวแรก + ... + 4 ตัวท้าย | `_get_account()` → `_mask_api_key()` |
| `account_email` | อ่านจากไฟล์ที่ชี้ด้วย env var `CLAUDE_USER_SETTINGS` → key `env.OTEL_RESOURCE_ATTRIBUTES` → parse ค่า `user.email=...` — โหลดครั้งเดียวตอน startup | `_load_account_email()` |
| `ip_address` | HTTP request header `x-forwarded-for` (ถ้ามี) หรือ `request.client.host` | `_get_client_ip()` |
| `source` | HTTP request header `user-agent` ที่ Claude Code ส่งมา | `request.headers.get("user-agent")` |
| `model` | Request body JSON field `model` ที่ Claude Code ส่งไป Anthropic | `body.get("model")` |
| `status_code` | HTTP status code จาก Anthropic upstream | `r.status_code` / hardcode `200` ใน streaming path |
| `input_tokens` | SSE event type `message_start` → `message.usage.input_tokens` | `parse_sse_log()` บรรทัด 204 |
| `output_tokens` | SSE event type `message_delta` → `usage.output_tokens` | `parse_sse_log()` บรรทัด 212 |
| `cache_creation_input_tokens` | SSE event type `message_start` → `message.usage.cache_creation_input_tokens` | `parse_sse_log()` บรรทัด 205 |
| `cache_read_input_tokens` | SSE event type `message_start` → `message.usage.cache_read_input_tokens` | `parse_sse_log()` บรรทัด 206 |
| `prompt` | Request body JSON field `messages[]` — เอาเฉพาะ **user message ล่าสุด** แล้วลบ XML tags ที่ Claude Code inject (`<system-reminder>`, `<ide_opened_file>` ฯลฯ) ออก | `extract_prompt_text()` |
| `response` | SSE event type `content_block_delta` → `delta.text` — เอาทุก chunk มาต่อกัน | `parse_sse_log()` บรรทัด 207-210 |

### SSE Events ที่ Parse (จาก Anthropic)

```
data: {"type": "message_start", "message": {"usage": {
    "input_tokens": 433,
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 0
}}}

data: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "สวัสดี"}}
data: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "ครับ..."}}

data: {"type": "message_delta", "usage": {"output_tokens": 13}}

data: [DONE]
```

---

## Cloudflare Worker

| Item | Value |
|------|-------|
| URL | `https://claude-prompt-logger.cloudflare-training3.workers.dev` |
| Auth | Header `X-Api-Key: <CF_WORKER_SECRET>` |
| Database | D1 `prompt-logger` |
| Tables | `prompt_logs`, `usage_logs` |
| Dashboard | GET / (HTML) |

### Payload ที่ proxy ส่งไป Worker

```json
// POST /api/prompt  (fire-and-forget ก่อน Anthropic ตอบ)
{
  "session_id": "ba6b4b4c-f8e3-4915-8d64-5de198e7a09d",
  "cwd": "C:\\Users\\project",
  "char_count": 42,
  "approx_tokens": 12,
  "prompt": "สวัสดีครับ คุณคือใคร?"
}

// POST /api/usage  (หลัง stream จบ พร้อม tokens ครบ)
{
  "session_id": "ba6b4b4c-f8e3-4915-8d64-5de198e7a09d",
  "model": "claude-sonnet-4-6",
  "input_tokens": 433,
  "output_tokens": 13,
  "cache_creation_input_tokens": 0,
  "cache_read_input_tokens": 0,
  "total_tokens": 446
}
```

---

## Environment Variables

| Variable | ตัวอย่าง | หมายเหตุ |
|----------|---------|---------|
| `LOG_FILE` | `/logs/proxy-logs.jsonl` | path ไฟล์ log ภายใน container |
| `CF_WORKER_URL` | `https://claude-prompt-logger.cloudflare-training3.workers.dev` | Worker URL |
| `CF_WORKER_SECRET` | `<secret>` | X-Api-Key authenticate กับ Worker |
| `CLAUDE_USER_SETTINGS` | `/etc/claude-user-settings.json` | path Claude Code user settings (อ่าน email) |
| `EMPLOYEE_CWD` | `C:\Users\project` | working dir พนักงาน ส่งไปพร้อม prompt log |
