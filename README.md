# Claude Code OTel Monitoring — Deploy บน Railway

## โครงสร้างไฟล์
```
├── Dockerfile              — OTel Collector (embed config ไว้แล้ว)
├── railway.toml            — Railway build config
├── otel-config.yaml        — OTel Collector config
├── docker-compose.yml      — สำหรับ run local เท่านั้น
├── init.sql                — Database schema
├── .env.example            — ตัวอย่าง environment variables
├── employee_settings.json  — config แจกพนักงาน
└── grafana/provisioning/
    └── datasources/
        └── datasource.yaml
```

---

## Deploy บน Railway (3 services แยกกัน)

Railway ไม่รองรับ docker-compose จาก GitHub โดยตรง ต้องสร้างแต่ละ service แยก

---

### SERVICE 1 — OTel Collector (จาก GitHub repo นี้)

1. ไป [railway.app](https://railway.app) → **New Project** → **Empty Project**
2. กด **+ New** → **GitHub Repo** → เลือก repo นี้
3. Railway จะ detect `Dockerfile` และ build อัตโนมัติ ✅
4. ใน **Settings** → **Networking** → Generate Domain
5. เปิด port `4317` (gRPC) และ `4318` (HTTP) ใน Variables:
```
PORT = 4318
```
6. จด URL ที่ได้ไว้ เช่น `your-app.up.railway.app`

---

### SERVICE 2 — PostgreSQL

1. ในโปรเจคเดิม กด **+ New** → **Database** → **Add PostgreSQL**
2. Railway สร้างให้อัตโนมัติ
3. ไปที่ PostgreSQL service → **Variables** → copy `DATABASE_URL`
4. รัน init.sql ใน **Data** tab หรือผ่าน psql:
```bash
psql $DATABASE_URL < init.sql
```

---

### SERVICE 3 — Grafana

1. กด **+ New** → **Docker Image**
2. ใส่ image: `grafana/grafana:latest`
3. ใน **Variables** เพิ่ม:
```
GF_SECURITY_ADMIN_USER         = admin
GF_SECURITY_ADMIN_PASSWORD     = (ตั้งรหัสแข็งแรง)
GF_USERS_ALLOW_SIGN_UP         = false
GF_DATABASE_TYPE               = postgres
GF_DATABASE_HOST               = (postgres host จาก Railway)
GF_DATABASE_NAME               = railway
GF_DATABASE_USER               = postgres
GF_DATABASE_PASSWORD           = (postgres password)
GF_SERVER_ROOT_URL             = https://(grafana-url).up.railway.app
```
4. ใน **Settings** → **Networking** → Generate Domain → เปิด port `3000`

---

## ตั้ง Grafana Datasource

1. เปิด Grafana → **Connections** → **Data Sources** → **Add**
2. เลือก **PostgreSQL**
3. ใส่:
   - Host: `(postgres host จาก Railway):5432`
   - Database: `railway`
   - User/Password: ตามที่ตั้งไว้
   - TLS: disable

---

## แก้ employee_settings.json แล้วแจกพนักงาน

แก้ 3 จุด:
1. `YOUR-APP.up.railway.app` → URL ของ OTel Collector จาก Railway
2. `EMPLOYEE_EMAIL@company.com` → email พนักงาน
3. `DEPARTMENT_NAME` → ชื่อแผนก

พนักงานนำไปวางที่:
- Mac/Linux: `~/.claude/settings.json`
- Windows: `%APPDATA%\Claude\settings.json`

---

## Run Local (ใช้ docker-compose)

```bash
cp .env.example .env
# แก้ .env ให้ครบ
docker-compose up -d
```

Grafana: http://localhost:3000

---

## ทดสอบ OTel Collector

```bash
curl https://YOUR-OTEL-URL.up.railway.app/v1/metrics
# ควรได้ response (อาจ 405 Method Not Allowed = ทำงานปกติ)
```
