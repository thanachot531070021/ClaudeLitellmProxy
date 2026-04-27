# Claude Code OTel Monitoring — Deploy บน Railway

## ไฟล์ในโปรเจกต์นี้
```
claude-otel-monitoring/
├── docker-compose.yml          — services ทั้งหมด (OTel + PostgreSQL + Grafana)
├── otel-config.yaml            — OTel Collector config
├── init.sql                    — Database schema (สร้างตารางอัตโนมัติ)
├── .env.example                — ตัวอย่าง environment variables
├── employee_settings.json      — config แจกพนักงาน (แก้ค่าก่อนแจก)
└── grafana/provisioning/
    └── datasources/
        └── datasource.yaml     — Grafana เชื่อม PostgreSQL อัตโนมัติ
```

---

## STEP 1 — อัปโหลดขึ้น GitHub

สร้าง repo ใหม่ใน GitHub แล้วอัปไฟล์ทั้งหมดขึ้นไป

---

## STEP 2 — สร้าง Railway Project

1. ไปที่ https://railway.app → Login ด้วย GitHub
2. กด **New Project** → **Deploy from GitHub repo**
3. เลือก repo ที่สร้าง
4. Railway จะ detect docker-compose.yml อัตโนมัติ

---

## STEP 3 — ตั้ง Environment Variables

ใน Railway → Service → **Variables** → เพิ่ม:

```
POSTGRES_USER       = admin
POSTGRES_PASSWORD   = (ตั้งรหัสแข็งแรง)
POSTGRES_CONN       = postgresql://admin:(password)@postgres:5432/claude_monitoring
GRAFANA_USER        = admin
GRAFANA_PASSWORD    = (ตั้งรหัสแข็งแรง)
```

---

## STEP 4 — Deploy และรอ

Railway จะ build และ start ทุก service อัตโนมัติ
รอ 2-3 นาที แล้วดู URL ที่ได้จาก Railway

---

## STEP 5 — แก้ employee_settings.json แล้วแจกพนักงาน

แก้ไข 2 จุด:
1. `YOUR-APP.up.railway.app` → URL จริงจาก Railway
2. `EMPLOYEE_EMAIL@company.com` → email พนักงานแต่ละคน
3. `DEPARTMENT_NAME` → ชื่อแผนก

พนักงานนำไฟล์นี้ไปวางที่:
- Mac/Linux: `~/.claude/settings.json`
- Windows: `%APPDATA%\Claude\settings.json`

แค่นี้เอง! Claude Code จะส่งข้อมูลมาที่ collector ทันทีที่เปิดใช้งาน

---

## STEP 6 — เข้า Grafana Dashboard

เปิด browser ไปที่:
```
https://YOUR-APP.up.railway.app:3000
```

Login: admin / (GRAFANA_PASSWORD ที่ตั้งไว้)

---

## ทดสอบว่าระบบทำงาน

```bash
# เช็ค OTel Collector
curl https://YOUR-APP.up.railway.app:4318/v1/metrics

# เช็คจาก Claude Code (รันในเครื่องพนักงานที่ตั้ง settings แล้ว)
claude --version
# แล้วลองใช้ claude code สักคำสั่ง → ดูใน Grafana ว่ามีข้อมูลเข้ามา
```

---

## ถ้าใช้ Sophos MDM

push ไฟล์ managed_settings ไปที่เครื่องพนักงานทุกเครื่อง:
- Mac: `/Library/Application Support/Claude/managed_settings.json`
- Windows: `C:\ProgramData\Claude\managed_settings.json`

พนักงานแก้ไขไม่ได้ และ OTel จะเปิดอัตโนมัติทุกเครื่อง
