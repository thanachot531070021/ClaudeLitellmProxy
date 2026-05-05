# Claude Litellm Proxy — Project Summary

## โปรเจคนี้คืออะไร

ระบบ monitor การใช้งาน Claude ของพนักงานแบบ real-time รองรับทุก client โดยพนักงานไม่ต้องลงซอฟต์แวร์ใดๆ เพิ่ม

---

## สถาปัตยกรรมทั้งหมด

```
+---------------------------+        +---------------------------+
|   เครื่องพนักงาน           |        |   Server (Docker)          |
|                           |        |                           |
|  Claude Code / VS Code    |------->|  proxy:8080  (FastAPI)    |
|  (ANTHROPIC_BASE_URL)     |        |      |                    |
|                           |        |      v                    |
|  Claude Desktop           |        |  api.anthropic.com        |
|  claude.ai web            |------->|                           |
|  (system proxy)           |        |  mitmproxy:8081           |
|                           |        |      |                    |
+---------------------------+        |      v                    |
                                     |  claude.ai                |
                                     |  api.anthropic.com        |
                                     |                           |
                                     |  otel-collector:4317/4318 |
                                     +---------------------------+
                                              |
                                              v
                                     +---------------------------+
                                     |  Cloudflare Worker        |
                                     |  D1: prompt_logs          |
                                     |      usage_logs           |
                                     +---------------------------+
```

### Client Support

| Client | Service | Port | วิธี |
|--------|---------|------|------|
| Claude Code CLI | proxy.py | 8080 | ตั้ง ANTHROPIC_BASE_URL |
| VS Code extension | proxy.py | 8080 | ตั้ง ANTHROPIC_BASE_URL |
| Claude Desktop | mitmproxy | 8081 | system proxy + cert |
| claude.ai web | mitmproxy | 8081 | system proxy + cert |

---

### Data Flow (Claude Code / VS Code)

```
1. Claude Code -> proxy:8080  POST /v1/messages
2. proxy สร้าง session_id (UUID)
3. proxy forward -> api.anthropic.com
4. Anthropic ส่ง SSE streaming response กลับมา
5. proxy stream ต่อให้ Claude Code ทันที
6. proxy parse SSE -> ได้ tokens ครบ (message_start + message_delta)
7. proxy เขียน logs/proxy-logs.jsonl
8. proxy ส่ง CF Worker ตามลำดับ:
   a. POST /api/prompt  (prompt + metadata)
   b. POST /api/usage   (token counts)
```

### Data Flow (Claude Desktop / claude.ai web)

```
1. Claude Desktop -> mitmproxy:8081  (ผ่าน system proxy)
2. mitmproxy decrypt HTTPS (ด้วย CA cert ที่ติดตั้งไว้)
3. mitmproxy forward -> claude.ai
4. claude.ai ส่ง SSE response กลับมา
5. mitmproxy parse SSE -> ได้ response text แต่ไม่มี token counts (*)
6. mitmproxy ประมาณ tokens จาก char count (chars ÷ 3.5)
7. mitmproxy เขียน logs/mitm_YYYY-MM-DD.jsonl
8. mitmproxy ส่ง CF Worker ใน thread เดียว ตามลำดับ:
   a. POST /api/prompt  (ส่งก่อนเสมอ)
   b. POST /api/usage   (ส่งหลัง prompt เสมอ — แก้ race condition)
```

> (*) **ข้อจำกัด:** claude.ai web interface ไม่ส่ง token counts ใน SSE stream มาที่ browser
> ทั้ง `message_start` และ `message_delta` ไม่มี `usage` field — ตรวจสอบแล้ว 2026-05-05
> tokens ที่บันทึกจึงเป็น **ค่าประมาณ** (approximate) ไม่ใช่ค่าจริง

---

## SSE Format จาก claude.ai (ตรวจสอบ 2026-05-05)

event types ที่ส่งมา: `message_start`, `content_block_start`, `content_block_delta`, `content_block_stop`, `message_delta`, `message_limit`, `message_stop`

```jsonc
// message_start — ไม่มี usage field (ต่างจาก api.anthropic.com)
{"type":"message_start","message":{"id":"chatcompl_...","model":"","content":[],...}}

// content_block_delta — มี response text ปกติ
{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"..."}}

// message_delta — ไม่มี usage.output_tokens (ต่างจาก api.anthropic.com)
{"type":"message_delta","delta":{"stop_reason":"end_turn",...}}

// message_limit — usage limit ของ account (5h/7d window) ไม่ใช่ token count
{"type":"message_limit","message_limit":{"windows":{"5h":{"utilization":0.86},...}}}
```

---

## ไฟล์สำคัญ

| ไฟล์ | หน้าที่ |
|------|---------|
| `proxy.py` | FastAPI proxy — Claude Code, parse SSE, log, ส่งไป CF Worker |
| `mitm_addon.py` | mitmproxy addon — Claude Desktop + claude.ai |
| `Dockerfile.proxy` | Build Python 3.12 image สำหรับ proxy.py |
| `Dockerfile.mitm` | Build Python + mitmproxy image |
| `docker-compose.yml` | 3 services: proxy:8080, mitmproxy:8081, otel-collector:4317/4318 |
| `otel-config.yaml` | Config OpenTelemetry Collector |
| `.env` | Secrets (CF_WORKER_SECRET, CF_WORKER_URL) |
| `.env.example` | Template สำหรับ .env |
| `setup-employee.ps1` | Script ติดตั้งบนเครื่องพนักงาน (ครั้งเดียว) |
| `certs/` | CA cert ที่ mitmproxy สร้าง — share กับ proxy เพื่อ serve ที่ /cert |
| `logs/` | Log files จากทุก service |
| `docs/` | เอกสาร |

---

## วิธีรัน Server

```bash
# 1. สร้าง .env
cp .env.example .env
# แก้ CF_WORKER_SECRET และ CF_WORKER_URL ใน .env

# 2. รัน
docker compose up --build -d

# ตรวจสอบ services
docker compose ps
```

ผลที่ได้:
```
proxy          Up   0.0.0.0:8080->8080/tcp
mitmproxy      Up   0.0.0.0:8081->8081/tcp
otel-collector Up   0.0.0.0:4317-4318->4317-4318/tcp
```

---

## วิธีติดตั้งบนเครื่องพนักงาน (ครั้งเดียว)

### วิธีที่ 1 — รัน Script (แนะนำ)

```powershell
# รันได้เลย ไม่ต้อง Admin ก่อน — script จะ pop UAC ให้เอง
.\setup-employee.ps1 -ServerIp "192.168.1.100" -Email "name@company.com"
```

script ทำให้อัตโนมัติ:
1. ตรวจสอบสิทธิ์ — ถ้าไม่ใช่ Admin จะ pop UAC prompt ให้กด Yes
2. ตั้ง `ANTHROPIC_BASE_URL=http://<server>:8080` ใน Claude Code settings.json
3. Download CA cert จาก `http://<server>:8080/cert`
4. ติดตั้ง cert ใน Windows Trusted Root CA
5. ตั้ง Windows system proxy -> `<server>:8081`

### วิธีที่ 2 — ทำเองทีละขั้น

```powershell
# (ต้องรัน PowerShell as Admin)

# 1. Download + ติดตั้ง cert
Invoke-WebRequest -Uri "http://<server>:8080/cert" -OutFile "$env:TEMP\ca.crt"
Import-Certificate -FilePath "$env:TEMP\ca.crt" -CertStoreLocation Cert:\LocalMachine\Root

# 2. ตั้ง system proxy
$reg = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings"
Set-ItemProperty $reg -Name ProxyEnable -Value 1
Set-ItemProperty $reg -Name ProxyServer  -Value "<server>:8081"

# 3. ตั้ง Claude Code
# เพิ่มใน %USERPROFILE%\.claude\settings.json
# "env": { "ANTHROPIC_BASE_URL": "http://<server>:8080" }
```

### ถ้าใช้แค่ Claude Code (ไม่ใช้ Claude Desktop)

```powershell
.\setup-employee.ps1 -ServerIp "192.168.1.100" -Email "name@company.com" -SkipDesktop
```

---

## Log Files — อยู่ที่ไหน ดูยังไง

### ตำแหน่งไฟล์

| ไฟล์ | มาจาก | รูปแบบ |
|------|-------|--------|
| `logs/proxy-logs.jsonl` | proxy.py (Claude Code) | 1 JSON ต่อ request |
| `logs/mitm_YYYY-MM-DD.jsonl` | mitm_addon.py (Claude Desktop) | 1 JSON ต่อ request แยกรายวัน |
| `logs/otel-logs.jsonl` | otel-collector | telemetry metrics |

### วิธีดู Log แบบ real-time

```bash
# ดู Claude Code logs
docker compose exec proxy tail -f /logs/proxy-logs.jsonl

# ดู Claude Desktop logs (วันนี้)
docker compose exec mitmproxy tail -f /logs/mitm_$(date +%Y-%m-%d).jsonl

# ดูทั้งคู่พร้อมกัน
docker compose logs -f proxy mitmproxy
```

### วิธีดู Log บน Windows (host)

```powershell
# ดูแบบ real-time
Get-Content ".\logs\proxy-logs.jsonl" -Wait -Tail 10

# ดูทั้งหมด + format สวย (ต้องมี jq หรือใช้ PowerShell)
Get-Content ".\logs\proxy-logs.jsonl" | ForEach-Object { $_ | ConvertFrom-Json } | Select-Object timestamp, client, model, input_tokens, output_tokens, cost_usd | Format-Table
```

### Format ของ Log — proxy-logs.jsonl (Claude Code)

```json
{
  "timestamp": "2026-05-05T10:29:08+00:00",
  "client": "claude-code",
  "machine_name": "SERVER-HOSTNAME",
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
  "total_tokens": 446,
  "cost_usd": 0.00052,
  "prompt": "สวัสดีครับ คุณคือใคร?",
  "response": "สวัสดีครับ ฉันคือ Claude..."
}
```

### Format ของ Log — mitm_YYYY-MM-DD.jsonl (Claude Desktop)

```json
{
  "id": "uuid-v4",
  "timestamp": "2026-05-05T10:37:47.998672Z",
  "client": "claude-desktop",
  "machine_name": "CONTAINER-HOSTNAME",
  "ip_address": "172.21.0.1",
  "account_email": "",
  "model": "claude-sonnet-4-6",
  "prompt": "วันอาทิตย์",
  "prompt_chars": 10,
  "response_chars": 66,
  "input_tokens": 2,
  "output_tokens": 18,
  "cache_creation_tokens": 0,
  "cache_read_tokens": 0,
  "total_tokens": 20,
  "cost_usd": 0.000276
}
```

> **หมายเหตุ:** `input_tokens` และ `output_tokens` ใน mitm log เป็น **ค่าประมาณ** (chars ÷ 3.5)
> เพราะ claude.ai ไม่ส่ง token counts ใน SSE stream

### ที่มาของแต่ละ Field (proxy-logs.jsonl)

| Field | มาจากไหน |
|-------|---------|
| `timestamp` | `datetime.now(UTC)` ตอน write log |
| `client` | `_detect_client()` — อ่านจาก header: user-agent, anthropic-client-name, x-app |
| `machine_name` | `socket.gethostname()` ของ server |
| `account` | header `x-api-key` / `Authorization: Bearer` — masked (8+...+4 ตัว) |
| `account_email` | ไฟล์ที่ชี้ด้วย `CLAUDE_USER_SETTINGS` -> `OTEL_RESOURCE_ATTRIBUTES` -> `user.email=` |
| `ip_address` | header `x-forwarded-for` หรือ `request.client.host` |
| `source` | header `user-agent` |
| `model` | request body field `model` |
| `input_tokens` | SSE event `message_start` -> `message.usage.input_tokens` (ค่าจริงจาก Anthropic) |
| `output_tokens` | SSE event `message_delta` -> `usage.output_tokens` (ค่าจริงจาก Anthropic) |
| `cache_creation_input_tokens` | SSE event `message_start` -> `usage.cache_creation_input_tokens` |
| `cache_read_input_tokens` | SSE event `message_start` -> `usage.cache_read_input_tokens` |
| `total_tokens` | `input + output + cache_creation + cache_read` |
| `cost_usd` | `_calc_cost()` — คำนวณจาก pricing table ตาม model tier |
| `prompt` | user message ล่าสุดจาก request body — ตัด XML tags ออก |
| `response` | ต่อ chunk จาก SSE event `content_block_delta` ทุกอัน |

### ที่มาของแต่ละ Field (mitm_YYYY-MM-DD.jsonl)

| Field | มาจากไหน |
|-------|---------|
| `id` | UUID v4 สร้างใหม่ต่อ request |
| `timestamp` | `datetime.utcnow()` ตอน write log |
| `client` | `_detect_client()` — ดู headers เหมือน proxy.py |
| `machine_name` | hostname ของ mitmproxy container |
| `ip_address` | `flow.client_conn.peername[0]` (IP เครื่องพนักงาน) |
| `account_email` | env var `ACCOUNT_EMAIL` หรืออ่านจาก `CLAUDE_USER_SETTINGS` |
| `model` | request body field `model` |
| `prompt` | `_extract_prompt_desktop()` — รองรับ messages array, prompt string, text field |
| `input_tokens` | **ประมาณ** `len(prompt) ÷ 3.5` (claude.ai ไม่ส่ง usage ใน SSE) |
| `output_tokens` | **ประมาณ** `len(response) ÷ 3.5` |
| `cost_usd` | `_calc_cost()` — คำนวณจาก approximate tokens |

---

## Troubleshooting

### log ไม่มีขึ้นเลย (ทั้ง proxy และ mitmproxy)

```bash
# เช็ค services ว่ารันอยู่ไหม
docker compose ps

# ถ้า service ไม่ขึ้น rebuild
docker compose up --build -d
```

### Claude Code log ไม่ขึ้น (proxy-logs.jsonl)

| สาเหตุ | วิธีแก้ |
|-------|--------|
| ยังไม่ได้ตั้ง ANTHROPIC_BASE_URL | รัน setup-employee.ps1 หรือตั้งใน settings.json |
| settings.json ไม่ถูก path | ตรวจสอบที่ `%USERPROFILE%\.claude\settings.json` |
| proxy container ไม่ได้รัน | `docker compose ps` -> `docker compose up -d proxy` |

```bash
# ดู log ของ proxy container
docker compose logs proxy --tail=50
```

### Claude Desktop log ไม่ขึ้น (mitm_*.jsonl)

| สาเหตุ | วิธีแก้ |
|-------|--------|
| ยังไม่ได้ download cert | ดาวน์โหลดจาก `http://<server>:8080/cert` แล้วติดตั้ง |
| cert ไม่ได้ติดตั้งเป็น Trusted Root | รัน setup-employee.ps1 as Admin |
| system proxy ไม่ได้ตั้ง | ตั้งที่ Windows Settings -> Network -> Proxy |
| ดูผิดไฟล์ | ดูที่ `logs/mitm_YYYY-MM-DD.jsonl` ไม่ใช่ proxy-logs.jsonl |
| mitmproxy container ไม่รัน | `docker compose up -d mitmproxy` |

```bash
# ดู log ของ mitmproxy container
docker compose logs mitmproxy --tail=50
```

### prompt ไม่ขึ้นใน Cloudflare Worker Dashboard

**สาเหตุเดิม (แก้แล้ว):** `/api/prompt` รันใน daemon thread แยก ทำให้ `/api/usage` ถึง Worker ก่อนเสมอ

**ที่แก้แล้ว:** ทั้ง `/api/prompt` และ `/api/usage` รันใน thread เดียวกัน ตามลำดับ prompt → usage เสมอ

### tokens = 0 ใน mitm log (Claude Desktop)

**สาเหตุ:** claude.ai ไม่ส่ง usage ใน SSE stream (ไม่มี `usage` field ใน `message_start` หรือ `message_delta`)

**ที่แก้แล้ว:** ประมาณ tokens จาก `chars ÷ 3.5` เมื่อ SSE ไม่มี token counts

### Debug SSE Format (เปิดเมื่อต้องการ)

```yaml
# ใน docker-compose.yml เพิ่มใต้ mitmproxy environment:
- DEBUG_SSE=1
- PYTHONUNBUFFERED=1
```

จะ dump raw SSE ลงไฟล์ `logs/sse_debug.txt` ทุกครั้งที่มี request จาก Claude Desktop

### endpoint /cert ไม่ตอบสนอง (404)

```bash
# rebuild proxy (อาจ build ก่อนมีโค้ด /cert)
docker compose up --build -d proxy

# ทดสอบ
curl http://localhost:8080/cert -o test.crt && echo "OK"
```

### ติดตั้ง cert ไม่ได้ — Access Denied

script setup-employee.ps1 จะ pop UAC ให้อัตโนมัติ แค่กด **Yes**

ถ้า pop ไม่ขึ้น รัน PowerShell as Administrator เองแล้วรัน script ใหม่

### system proxy ทำให้ internet ใช้ไม่ได้

```powershell
# ปิด system proxy ชั่วคราว
$reg = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings"
Set-ItemProperty $reg -Name ProxyEnable -Value 0
```

ตรวจสอบว่า mitmproxy container รันอยู่ก่อนเปิด proxy กลับ

---

## Environment Variables

| Variable | Default | หมายเหตุ |
|----------|---------|---------|
| `CF_WORKER_URL` | - | Cloudflare Worker URL |
| `CF_WORKER_SECRET` | - | X-Api-Key authenticate กับ Worker |
| `LOG_FILE` | `/logs/proxy-logs.jsonl` | path log (proxy.py) |
| `LOG_DIR` | `/logs` | path log dir (mitm_addon.py) |
| `CERTS_DIR` | `/certs` | path CA cert dir (proxy.py serve /cert) |
| `CLAUDE_USER_SETTINGS` | - | path Claude Code settings อ่าน account email |
| `EMPLOYEE_CWD` | - | working dir พนักงาน ส่งไปพร้อม prompt log |
| `PYTHONUNBUFFERED` | - | ตั้งเป็น `1` เพื่อให้ print() ออก Docker logs ทันที |
| `DEBUG_SSE` | `0` | ตั้งเป็น `1` เพื่อ dump raw SSE ไว้ที่ `logs/sse_debug.txt` |

---

## Cloudflare Worker

| Item | Value |
|------|-------|
| URL | `https://claude-prompt-logger.cloudflare-training3.workers.dev` |
| Auth | Header `X-Api-Key: <CF_WORKER_SECRET>` |
| Database | D1 `prompt-logger` |
| Tables | `prompt_logs`, `usage_logs` |
| Dashboard | `GET /` (HTML) |
| Health check | `GET /health` |

### API Endpoints

| Endpoint | ส่งจาก | ข้อมูล | ลำดับ |
|----------|--------|--------|-------|
| `POST /api/prompt` | proxy.py, mitm_addon.py | prompt + metadata | ส่งก่อนเสมอ |
| `POST /api/usage` | proxy.py, mitm_addon.py | token usage | ส่งหลัง prompt เสมอ |

> **สำคัญ:** ต้องส่ง `/api/prompt` ก่อน `/api/usage` เสมอ เพราะ Worker ใช้ `session_id` ลิงก์ระหว่าง 2 records
> ถ้า usage ถึงก่อน prompt จะทำให้ prompt ไม่แสดงใน Dashboard
