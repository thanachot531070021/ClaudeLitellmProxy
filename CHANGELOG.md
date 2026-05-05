# Changelog

## 2026-04-30 — เพิ่ม fields และแก้ bug log ไม่บันทึก

### ปัญหาที่พบ
- Log file ไม่ถูกเขียนเมื่อรัน proxy ตรงบน Windows (ไม่ผ่าน Docker)
- Log entry ขาด context ของ request เช่น ใครส่ง, มาจากไหน

---

### สิ่งที่แก้ไข

#### 1. เพิ่ม fields ใหม่ใน log entry (`proxy.py`)

ทุก log entry ใน `proxy-logs.jsonl` ตอนนี้มีครบ 8 fields:

| Field | ที่มา | ตัวอย่าง |
|---|---|---|
| `timestamp` | UTC ISO 8601 | `2026-04-30T05:23:22.051229+00:00` |
| `account` | `x-api-key` header (masked) | `sk-ant-ap...aXyz` |
| `ip_address` | `X-Forwarded-For` → client IP | `192.168.1.10` |
| `source` | `User-Agent` header | `ClaudeCode/1.2.3 darwin arm64` |
| `model` | request body | `claude-sonnet-4-6` |
| `input_tokens` | response usage | `1024` |
| `output_tokens` | response usage | `256` |
| `prompt` | user message ล่าสุด (strip tags) | `"สวัสดี"` |
| `response` | response text | `"สวัสดีครับ"` |

#### 2. เพิ่ม helper functions (`proxy.py`)

```python
_get_client_ip(request)   # อ่าน X-Forwarded-For ก่อน fallback เป็น client IP
_mask_api_key(key)        # แสดง 8 ตัวแรก...4 ตัวท้าย
```

#### 3. แก้ bug: log ไม่ถูกเขียนบน Windows (`.env` + `proxy.py`)

**สาเหตุ:** `LOG_FILE` default คือ `/logs/proxy-logs.jsonl` ใช้ได้เฉพาะใน Docker (มี volume mount) เมื่อรันตรงบน Windows path นี้ไม่มีอยู่ → `write_log` fail silently

**แก้ไข:**
- `proxy.py` — เพิ่ม `load_dotenv()` ให้โหลด `.env` อัตโนมัติ
- `.env` — เพิ่ม `LOG_FILE=./logs/proxy-logs.jsonl` สำหรับรันตรง

> Docker ยังทำงานปกติ เพราะ `docker-compose.yml` override `LOG_FILE=/logs/proxy-logs.jsonl` เอง

#### 4. เพิ่ม meta ส่งไป CF Worker (`_push_prompt`)

ตอนนี้ `_push_prompt` ส่ง `account`, `ip_address`, `source` ไปพร้อมกับ prompt ด้วย

---

### ไฟล์ที่เปลี่ยนแปลง

| ไฟล์ | การเปลี่ยนแปลง |
|---|---|
| `proxy.py` | เพิ่ม `load_dotenv()`, `_get_client_ip()`, `_mask_api_key()`, meta dict, fields ใน log |
| `.env` | เพิ่ม `LOG_FILE=./logs/proxy-logs.jsonl` |
