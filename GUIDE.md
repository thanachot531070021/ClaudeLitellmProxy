# Claude Proxy — คู่มือติดตั้งและใช้งาน

## ภาพรวม

Proxy นี้ทำหน้าที่คั่นกลางระหว่าง **Claude Code / Client** กับ **Anthropic API**
ทุก request จะถูก forward ไปยัง Anthropic ตามปกติ แต่ proxy จะดักเก็บข้อมูลไว้ก่อน

```
Claude Code ──► Proxy (port 8080) ──► api.anthropic.com
                    │
                    ├── บันทึก logs/proxy-logs.jsonl
                    └── ส่งข้อมูลไป Cloudflare Worker (ถ้าตั้งค่าไว้)
```

---

## โครงสร้างไฟล์

```
ClaudeLitellmProxy/
├── proxy.py              # ตัว proxy หลัก
├── .env                  # ค่า config (สร้างจาก .env.example)
├── .env.example          # template สำหรับ .env
├── requirements.txt      # Python dependencies
├── Dockerfile.proxy      # Docker image สำหรับ proxy
├── docker-compose.yml    # รัน proxy + OTel collector พร้อมกัน
├── otel-config.yaml      # config สำหรับ OpenTelemetry Collector
└── logs/
    └── proxy-logs.jsonl  # log file (สร้างอัตโนมัติ)
```

---

## วิธีติดตั้ง

### วิธีที่ 1 — รันตรงด้วย Python (แนะนำสำหรับ dev)

**ขั้นตอนที่ 1 — ติดตั้ง dependencies**

```bash
pip install -r requirements.txt
```

**ขั้นตอนที่ 2 — ตั้งค่า .env**

```bash
# Windows
copy .env.example .env

# Mac/Linux
cp .env.example .env
```

แก้ไขไฟล์ `.env`:

```env
LOG_FILE=./logs/proxy-logs.jsonl
CF_WORKER_URL=https://your-worker.workers.dev   # ถ้ามี
CF_WORKER_SECRET=your-secret                    # ถ้ามี
ANTHROPIC_API_KEY=sk-ant-...                    # (optional) ใช้เป็น default key
```

**ขั้นตอนที่ 3 — รัน proxy**

```bash
# รันจาก directory ของโปรเจกต์
cd ClaudeLitellmProxy
py -m uvicorn proxy:app --host 0.0.0.0 --port 8080
```

proxy จะเปิดที่ `http://localhost:8080`

---

### วิธีที่ 2 — รันด้วย Docker Compose (แนะนำสำหรับ production)

**ขั้นตอนที่ 1 — ตั้งค่า .env**

```bash
copy .env.example .env
```

แก้ไข `CF_WORKER_SECRET` ใน `.env`

**ขั้นตอนที่ 2 — Build และรัน**

```bash
docker compose up -d --build
```

ตรวจสอบว่ารันอยู่:

```bash
docker compose ps
docker compose logs -f proxy
```

หยุด:

```bash
docker compose down
```

---

## การตั้งค่า Claude Code ให้ใช้ proxy

เปิดไฟล์ config ของ Claude Code (`~/.claude/settings.json` หรือ `claude_desktop_config.json`) แล้วเพิ่ม:

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://localhost:8080",
    "ANTHROPIC_API_KEY": "sk-ant-your-real-key"
  }
}
```

หลังจากนั้น Claude Code จะส่ง request ผ่าน proxy โดยอัตโนมัติ

---

## Environment Variables

| ตัวแปร | ค่า default | คำอธิบาย |
|---|---|---|
| `LOG_FILE` | `logs/proxy-logs.jsonl` | path ของ log file (relative = จาก dir ของ proxy.py) |
| `CF_WORKER_URL` | `""` (ปิด) | URL ของ Cloudflare Worker สำหรับส่งข้อมูล realtime |
| `CF_WORKER_SECRET` | `""` | Secret key สำหรับ authenticate กับ CF Worker |
| `EMPLOYEE_CWD` | `""` | ชื่อหรือ path ของ employee ที่ส่งข้อมูลไป CF Worker |

> ถ้า `CF_WORKER_URL` ว่างเปล่า → proxy ทำงานแบบ **log-file only** ไม่ส่งข้อมูลออกไปที่ไหน

---

## Log File Format

ทุก request ที่สำเร็จ (HTTP 200) จะถูกบันทึกเป็น JSON 1 บรรทัดใน `logs/proxy-logs.jsonl`

```json
{
  "timestamp":   "2026-04-30T05:23:22.051229+00:00",
  "account":     "sk-ant-ap...aXyz",
  "ip_address":  "192.168.1.10",
  "source":      "ClaudeCode/1.2.3 darwin arm64",
  "model":       "claude-sonnet-4-6",
  "input_tokens":  1024,
  "output_tokens":  256,
  "cache_creation_input_tokens": 0,
  "cache_read_input_tokens":     4096,
  "prompt":      "ช่วยเขียน function สำหรับ...",
  "response":    "นี่คือ function ที่ต้องการ..."
}
```

| Field | คำอธิบาย |
|---|---|
| `timestamp` | เวลา UTC ที่ response กลับมา |
| `account` | API key ที่ใช้ (masked — แสดงแค่ต้นและท้าย) |
| `ip_address` | IP ของ client ที่ส่ง request (รองรับ X-Forwarded-For) |
| `source` | User-Agent ของ client เช่น `ClaudeCode/1.x.x` |
| `model` | Model ที่ใช้ |
| `input_tokens` | จำนวน input tokens |
| `output_tokens` | จำนวน output tokens |
| `cache_creation_input_tokens` | tokens ที่สร้าง cache ใหม่ |
| `cache_read_input_tokens` | tokens ที่อ่านจาก cache |
| `prompt` | user message ล่าสุด (ตัด system tags ออกแล้ว) |
| `response` | text response จาก Claude |

> Log จะถูกเขียน**เฉพาะ request ที่ได้ HTTP 200** เท่านั้น — request ที่ fail (401, 429, 5xx) จะไม่มี log entry

---

## การทำงานภายใน proxy.py

### 1. การโหลด Config

```python
_BASE_DIR = Path(__file__).parent
load_dotenv(_BASE_DIR / ".env")   # หา .env จาก dir ของ proxy.py เสมอ
```

ใช้ `Path(__file__).parent` แทน current working directory เพื่อให้ proxy หา `.env` ได้ถูก
ไม่ว่าจะ start จาก directory ไหนก็ตาม

### 2. Request Flow

```
request เข้ามา
    │
    ├── parse body + extract meta (account, ip, source)
    ├── สร้าง session_id (UUID)
    ├── ถ้า CF_WORKER_URL ตั้งไว้ → push prompt ไป Worker ทันที (async, ไม่รอ)
    │
    ├── [streaming] → forward แบบ streaming + เก็บ raw bytes ทั้งหมด
    │                  → เมื่อ stream จบ → parse SSE → write_log
    │
    └── [non-streaming] → forward ปกติ
                          → ถ้า 200 → parse response → write_log
```

### 3. Streaming vs Non-streaming

**Streaming** (`"stream": true`) — Claude Code ใช้โดยค่าเริ่มต้น
- proxy เปิด connection ไว้และส่ง bytes ทีละ chunk ไปให้ client
- เก็บ raw bytes ทั้งหมดไว้ใน buffer
- เมื่อ stream จบจึง parse SSE events เพื่อดึง tokens และ response text

**Non-streaming** (`"stream": false`)
- รอ response ครบก่อนจึง forward ทั้งก้อน
- ง่ายกว่าแต่ client ต้องรอนานกว่า

### 4. Header Filtering

```python
SKIP_HEADERS = {
    "host", "content-length", "transfer-encoding",
    "connection", "keep-alive",
    "accept-encoding", "content-encoding",
}
```

Headers เหล่านี้จะถูกกรองออกก่อน forward ไป Anthropic เพื่อป้องกัน
HTTP protocol mismatch ระหว่าง client ↔ proxy ↔ upstream

### 5. API Key Masking

```python
def _mask_api_key(key: str) -> str:
    return key[:8] + "..." + key[-4:]
    # sk-ant-ap...aXyz
```

API key ที่เก็บใน log จะถูก mask ไว้ เห็นแค่ 8 ตัวแรกและ 4 ตัวท้าย
เพื่อให้ระบุได้ว่าใช้ key ไหน แต่ไม่เปิดเผย key จริง

### 6. Cloudflare Worker Integration

เมื่อตั้งค่า `CF_WORKER_URL`:

- **`/api/prompt`** — รับ prompt ทันทีที่ request เข้ามา (ก่อนรอ response)
  - ส่ง: `session_id`, `prompt`, `account`, `ip_address`, `source`, `char_count`, `approx_tokens`

- **`/api/usage`** — รับ usage data หลัง response กลับมา
  - ส่ง: `session_id`, `model`, `input_tokens`, `output_tokens`, `cache_*`, `total_tokens`

ทั้งสองส่งแบบ async — ไม่บล็อก response ที่ส่งกลับ client

---

## ดู Log แบบ real-time

```bash
# Windows (PowerShell)
Get-Content logs\proxy-logs.jsonl -Wait -Tail 5

# Mac/Linux
tail -f logs/proxy-logs.jsonl
```

อ่าน log แบบ pretty print:

```bash
# ดู 10 entries ล่าสุด (ไม่แสดง prompt/response)
tail -10 logs/proxy-logs.jsonl | python -c "
import sys, json
for line in sys.stdin:
    d = json.loads(line)
    print(d['timestamp'], d.get('account',''), d.get('model',''),
          'in:', d.get('input_tokens'), 'out:', d.get('output_tokens'))
"
```

---

## Troubleshooting

| ปัญหา | สาเหตุ | แก้ไข |
|---|---|---|
| Log ไม่ถูกบันทึก | `LOG_FILE` path ผิด | ตรวจสอบ `.env` มี `LOG_FILE=./logs/proxy-logs.jsonl` |
| Log ไม่ถูกบันทึก | proxy รันจาก dir อื่น | ปกติแก้ไขแล้ว — `proxy.py` resolve path จาก dir ของตัวเอง |
| CF Worker ไม่รับข้อมูล | `CF_WORKER_SECRET` ผิด | ตรวจสอบ secret ใน `.env` ตรงกับ Worker |
| 502 Bad Gateway | เชื่อมต่อ Anthropic ไม่ได้ | ตรวจสอบ internet และ firewall |
| Log มีแต่ `input=0 output=0` | request ได้ 401 (API key ผิด) | ใส่ API key จริงใน Claude Code settings |
