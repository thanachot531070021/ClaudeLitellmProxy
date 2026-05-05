# Claude Litellm Proxy

ระบบ proxy สำหรับ monitor การใช้งาน Claude Code ของพนักงาน ดัก prompt + token usage โดยไม่ต้องแก้ไข Claude Code ฝั่งพนักงาน ส่งข้อมูลไป Cloudflare D1 ผ่าน Worker API แบบ real-time

---

## Quick Start

```bash
# 1. Setup
cp .env.example .env
# แก้ CF_WORKER_SECRET ให้เป็น key จริงจาก claude-prompt-logger Worker

# 2. Run
docker compose up --build -d

# 3. Test
curl -H "X-Api-Key: $(grep CF_WORKER_SECRET .env | cut -d= -f2)" \
  https://claude-prompt-logger.cloudflare-training3.workers.dev/health
```

---

## สถาปัตยกรรมระบบ

```
┌──────────────────────────────────────────────────┐
│           เครื่องพนักงาน                          │
│                                                 │
│  Claude Code CLI                                 │
│      │ POST /v1/messages                         │
│      │ ANTHROPIC_BASE_URL=http://<server>:8080   │
│      └──────────────────────────────────────────►
└──────────────────────────────────────────────────┘
                     ▲
                     │ SSE stream response
                     │
           ┌─────────┴──────────┐
           │                    │
           ▼                    ▼
    proxy:8080            (local files)
    ┌──────────────────────────────────┐
    │ 1. รับ request → generate UUID    │
    │ 2. POST /api/prompt              │
    │    (ก่อน Anthropic ตอบ)          │
    │ 3. stream → Anthropic            │
    │ 4. parse SSE → get tokens        │
    │ 5. POST /api/usage               │
    │    (พร้อม token ครบ)             │
    │ 6. เขียน proxy-logs.jsonl (backup)
    └──────────────────┬──────────────────┘
                       │
                       │ POST /api/prompt
                       │ POST /api/usage
                       │
                       ▼
      ┌────────────────────────────────┐
      │ Cloudflare Worker              │
      │ claude-prompt-logger           │
      │ https://...workers.dev         │
      │                                │
      │ D1: prompt-logger              │
      │ - prompt_logs (2 columns)      │
      │ - usage_logs (8 columns)       │
      └────────────────────────────────┘
```

### Data Flow สำหรับ 1 Prompt Request

```
1. Claude Code → proxy:8080 POST /v1/messages
2. proxy สร้าง session_id (UUID)
3. proxy ส่ง prompt ไป CF Worker → POST /api/prompt (ไม่รอ)
4. proxy forward request ไป api.anthropic.com
5. Anthropic ส่ง streaming response (SSE)
6. proxy stream ต่อให้ Claude Code ทันที
7. proxy parse SSE events → ได้ tokens ครบ
8. proxy เขียน proxy-logs.jsonl (local backup)
9. proxy ส่ง usage ไป CF Worker → POST /api/usage (รอ response)
10. Worker เก็บ 2 record ลง D1:
    - prompt_logs.insert({ session_id, prompt, ... })
    - usage_logs.insert({ session_id, model, tokens, ... })
```

---

## Cloudflare Worker

| Item | Value |
|------|-------|
| **URL** | `https://claude-prompt-logger.cloudflare-training3.workers.dev` |
| **Dashboard** | `GET /` (HTML server-rendered) |
| **Auth** | Header `X-Api-Key: <WORKER_SECRET>` (ดูไฟล์ INTEGRATION.md) |
| **Database** | D1 `prompt-logger` |

### API Endpoints

| Endpoint | ส่งจาก | ข้อมูล | Response |
|----------|--------|--------|----------|
| `POST /api/prompt` | proxy.py | prompt ที่ผู้ใช้พิมพ์ | `{ "ok": true }` |
| `POST /api/usage` | proxy.py | token usage หลัง model ตอบ | `{ "ok": true }` |
| `GET /health` | manual test | - | `{ "ok": true }` |
| `GET /` | browser | - | HTML dashboard (50 items) |

---

## Components

### proxy (port 8080)

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
  "input_tokens": 433,
  "output_tokens": 13,
  "cache_creation_input_tokens": 0,
  "cache_read_input_tokens": 0,
  "prompt": "สวัสดีครับ คุณคือใคร?",
  "response": "สวัสดีครับ ฉันคือ Claude AI assistant ที่..."
}
```

**Payload ไป CF Worker:**

```json
// POST /api/prompt (fire-and-forget, ไม่รอ response)
{
  "session_id": "ba6b4b4c-f8e3-4915-8d64-5de198e7a09d",
  "cwd": "C:\\Users\\project",
  "char_count": 42,
  "approx_tokens": 12,
  "prompt": "สวัสดีครับ คุณคือใคร?"
}

// POST /api/usage (รอ response OK)
{
  "session_id": "ba6b4b4c-f8e3-4915-8d64-5de198e7a09d",
  "model": "claude-sonnet-4-6",
  "input_tokens": 10,
  "output_tokens": 307,
  "cache_creation_input_tokens": 114097,
  "cache_read_input_tokens": 116960,
  "total_tokens": 231374
}
```

---

## Environment Variables

| Variable | ตัวอย่าง | หมายเหตุ |
|----------|---------|---------|
| `LOG_FILE` | `/logs/proxy-logs.jsonl` | path ไฟล์ log (ภายใน container) |
| `CF_WORKER_URL` | `https://claude-prompt-logger.cloudflare-training3.workers.dev` | Worker URL |
| `CF_WORKER_SECRET` | `Softdebut888` | X-Api-Key value ตั้งใน Worker → Settings → Variables/Secrets |
| `ANTHROPIC_API_KEY` | `sk-ant-...` | API key ของ Anthropic (ตั้งใน .env หรือ system env) |

---

## Setup & Deploy

### 1. สร้างไฟล์ `.env`

```bash
cp .env.example .env
```

**แก้ไขค่า:**
- `CF_WORKER_URL` = URL ของ `claude-prompt-logger` Worker
- `CF_WORKER_SECRET` = key จาก Worker Settings → Variables/Secrets

### 2. Rebuild Docker

```bash
docker compose down
docker compose up --build -d
```

### 3. ทดสอบ

```bash
# เช็ค health
curl -H "X-Api-Key: $(grep CF_WORKER_SECRET .env | cut -d= -f2)" \
  https://claude-prompt-logger.cloudflare-training3.workers.dev/health

# ดู logs
docker compose logs -f proxy
```

### 4. ตั้ง Claude Code

ใส่คำสั่งใน `employee_settings.json` ของ Claude Code:

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://<server_ip>:8080"
  }
}
```

**หรือตั้ง environment variables ใน shell:**

```bash
export ANTHROPIC_BASE_URL=http://<server_ip>:8080
```

---

## Files

| ไฟล์ | รายละเอียด |
|------|---------|
| [proxy.py](proxy.py) | FastAPI proxy — รับ + forward + ส่ง log |
| [Dockerfile.proxy](Dockerfile.proxy) | Python 3.12 slim + FastAPI + httpx |
| [docker-compose.yml](docker-compose.yml) | 1 service: proxy |
| [requirements.txt](requirements.txt) | Dependencies: anthropic, fastapi, httpx, uvicorn |
| [.env.example](.env.example) | Template environment variables |
| [logs/](logs/) | Volume mount สำหรับ proxy-logs.jsonl |
| [INTEGRATION.md](INTEGRATION.md) | API docs + D1 schema |

---

## ทดสอบ Request

### ผ่าน curl

```bash
# 1. ส่ง prompt
WORKER_KEY=$(grep CF_WORKER_SECRET .env | cut -d= -f2)
curl -H "X-Api-Key: $WORKER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"session_id":"test-1","cwd":"/tmp","char_count":50,"approx_tokens":14,"prompt":"hello"}' \
  https://claude-prompt-logger.cloudflare-training3.workers.dev/api/prompt

# 2. ส่ง usage
curl -H "X-Api-Key: $WORKER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"session_id":"test-1","model":"claude-sonnet-4-6","input_tokens":100,"output_tokens":50,"cache_creation_input_tokens":0,"cache_read_input_tokens":0,"total_tokens":150}' \
  https://claude-prompt-logger.cloudflare-training3.workers.dev/api/usage

# 3. ดูว่า DB มีข้อมูลแล้วหรือยัง
curl -H "X-Api-Key: $WORKER_KEY" \
  https://claude-prompt-logger.cloudflare-training3.workers.dev/
```

### ผ่าน Postman

ดูไฟล์ [INTEGRATION.md](INTEGRATION.md) สำหรับขั้นตอนอย่างละเอียด

---

## Troubleshooting

### proxy ส่งข้อมูลแล้ว แต่ Worker ได้ 401 Unauthorized

**สาเหตุ:** `CF_WORKER_SECRET` ใน `.env` ไม่ถูกต้อง หรือ secret ใน Cloudflare ไม่ได้ตั้ง

**วิธีแก้:**
1. เปิด Cloudflare Dashboard → Workers → `claude-prompt-logger` → Settings
2. ลงไปดู **Variables/Secrets** ได้ค่า API key
3. อัพเดต `.env` ให้เป็นค่านั้น
4. `docker compose up -d` (restart)

### Proxy logs มีแต่ไม่มีข้อมูล

**สาเหตุ:** Volume `/logs` ไม่มี permission write

**วิธีแก้:**
```bash
docker compose exec proxy chmod 777 /logs
```

### Stream หยุดหรือดำเนินการช้า

**สาเหตุ:** การส่ง `/api/usage` รอ response ค้าง (network issue)

**วิธีแก้:**
- ลดจำนวน retries (อยู่ที่ 3 ครั้ง)
- เพิ่ม timeout value ใน proxy.py (ปัจจุบัน 10 วินาที)

---

## หยุดและ Rebuild

```bash
# rebuild หลังแก้ proxy.py
docker compose up --build -d

# restart อย่างเดียว (แก้ .env)
docker compose up -d

# หยุดทั้งหมด
docker compose down
```
