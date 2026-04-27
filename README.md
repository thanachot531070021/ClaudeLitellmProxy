# LiteLLM Proxy — Deploy บน Railway

## ไฟล์ในโฟลเดอร์นี้
```
litellm-proxy/
├── Dockerfile          — สำหรับ build image
├── railway.toml        — config การ deploy บน Railway
├── config.yaml         — ตั้งค่า model และ settings
├── .env.example        — ตัวอย่าง environment variables
└── add_employee_keys.sh — สคริปต์สร้าง key ให้พนักงาน
```

---

## STEP 1 — สมัคร Railway
1. ไปที่ https://railway.app
2. กด "Start a New Project" → Login ด้วย GitHub
3. ไม่ต้องใส่บัตรเครดิต ได้ $5 credit ทันที

---

## STEP 2 — สร้าง Project และ Deploy

1. กด **"New Project"** → **"Deploy from GitHub repo"**
2. อัป folder นี้ขึ้น GitHub repo (หรือใช้ Railway CLI)
3. Railway จะ detect Dockerfile อัตโนมัติ

**หรือใช้ Railway CLI (ง่ายกว่า):**
```bash
# ติดตั้ง Railway CLI
npm install -g @railway/cli

# login
railway login

# สร้าง project และ deploy
cd litellm-proxy
railway init
railway up
```

---

## STEP 3 — เพิ่ม PostgreSQL

1. ใน Railway project → กด **"+ New"** → **"Database"** → **"PostgreSQL"**
2. Railway จะสร้าง DATABASE_URL ให้อัตโนมัติ
3. ไปที่ service LiteLLM → **Variables** → ตรวจว่า DATABASE_URL ถูก link แล้ว

---

## STEP 4 — ตั้ง Environment Variables

ใน Railway project → service LiteLLM → **Variables** → เพิ่ม:

| Variable | ค่า |
|---|---|
| `ANTHROPIC_API_KEY` | sk-ant-api03-xxxx (key จริงของบริษัท) |
| `LITELLM_MASTER_KEY` | sk-company-master-2025 (ตั้งเองได้) |
| `DATABASE_URL` | Railway link อัตโนมัติจาก PostgreSQL |

---

## STEP 5 — สร้าง Key ให้พนักงาน

แก้ไขไฟล์ add_employee_keys.sh:
- เปลี่ยน PROXY_URL เป็น URL จาก Railway
- เพิ่มชื่อพนักงานใน EMPLOYEES
- รัน: bash add_employee_keys.sh

---

## STEP 6 — แจก Key ให้พนักงานตั้งบนเครื่อง

**Mac/Linux** — ใส่ใน ~/.zshrc หรือ ~/.bashrc:
```bash
export ANTHROPIC_BASE_URL=https://YOUR-APP.up.railway.app
export ANTHROPIC_API_KEY=sk-xxxx-ของ-พนักงาน-คนนั้น
source ~/.zshrc
```

**Windows** — System Properties → Environment Variables:
```
ANTHROPIC_BASE_URL = https://YOUR-APP.up.railway.app
ANTHROPIC_API_KEY  = sk-xxxx-ของ-พนักงาน-คนนั้น
```

---

## ดู Dashboard และ Log

เปิด browser ไปที่:
```
https://YOUR-APP.up.railway.app/ui
```
Login ด้วย LITELLM_MASTER_KEY
ดูได้: token ต่อคน, cost, prompt history, model usage

---

## ทดสอบว่า Proxy ทำงาน

```bash
curl https://YOUR-APP.up.railway.app/health
```
ต้องได้ {"status":"healthy"}
