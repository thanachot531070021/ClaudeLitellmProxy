# Docker — คู่มือใช้งาน

## ภาพรวม Services

```
docker-compose.yml
├── proxy            → build จาก Dockerfile.proxy  (port 8080)
└── otel-collector   → pull image สำเร็จรูป         (port 4317, 4318)
```

---

## ไฟล์ที่เกี่ยวข้อง

| ไฟล์ | ใช้กับ | หน้าที่ |
|------|--------|---------|
| `docker-compose.yml` | ทั้งสอง service | กำหนด services, ports, volumes, env |
| `Dockerfile.proxy` | proxy | build Python + FastAPI app |
| `Dockerfile` | otel-collector | build custom image ที่ bake config ข้างใน (ยังไม่ได้ใช้งานใน compose) |
| `otel-config.yaml` | otel-collector | config receiver/processor/exporter |

---

## คำสั่งที่ใช้บ่อย

### เริ่มระบบ

```bash
# ครั้งแรก หรือหลังแก้ไข proxy.py / Dockerfile.proxy
docker compose up --build -d

# ถ้าไม่ได้แก้ไข code (แค่ restart)
docker compose up -d
```

### หยุด / ลบ

```bash
# หยุด services (container ยังอยู่)
docker compose stop

# หยุด + ลบ container (image ยังอยู่)
docker compose down

# ลบทุกอย่างรวม image และ volume (reset สมบูรณ์)
docker compose down --rmi all --volumes
```

### Restart บาง service

```bash
docker compose restart proxy
docker compose restart otel-collector
```

### ดู logs

```bash
# ดู real-time ทุก service
docker compose logs -f

# ดูเฉพาะ proxy
docker compose logs -f proxy

# ดูเฉพาะ otel-collector
docker compose logs -f otel-collector

# ดูย้อนหลัง 50 บรรทัด
docker compose logs --tail=50 proxy
```

### ตรวจสอบสถานะ

```bash
# ดู status ทุก container
docker compose ps

# ดู resource usage (CPU, RAM)
docker stats
```

### Rebuild เฉพาะ proxy (หลังแก้ code)

```bash
docker compose up --build -d proxy
```

---

## Volumes — ไฟล์ที่ share ระหว่าง Host กับ Container

| Host (เครื่องเรา) | Container | ใช้กับ |
|-------------------|-----------|--------|
| `./logs` | `/logs` | proxy เขียน `proxy-logs.jsonl` ที่นี่ |
| `./logs` | `/var/log/claude-usage` | otel-collector เขียน `otel-logs.jsonl` ที่นี่ |
| `./otel-config.yaml` | `/etc/otel/config.yaml` | otel-collector อ่าน config จากที่นี่ |

> **สำคัญ:** ไฟล์ log อยู่ที่ `./logs/` บน host — ลบ container แล้ว log ยังอยู่ครบ

---

## Ports

| Port | Service | Protocol | ใช้กับ |
|------|---------|----------|--------|
| 8080 | proxy | HTTP | Claude Code ส่ง API request มาที่นี่ (`ANTHROPIC_BASE_URL`) |
| 4317 | otel-collector | gRPC | รับ OTel telemetry แบบ gRPC |
| 4318 | otel-collector | HTTP | รับ OTel telemetry แบบ HTTP (`OTEL_EXPORTER_OTLP_ENDPOINT`) |

---

## Environment Variables (proxy)

กำหนดใน `docker-compose.yml`:

| Variable | ค่า default | ความหมาย |
|----------|-------------|----------|
| `LOG_FILE` | `/logs/proxy-logs.jsonl` | path ที่เขียน log ข้างใน container |

---

## Dockerfile.proxy — วิธีทำงาน

```dockerfile
FROM python:3.12-slim          # base image Python เบาๆ
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt   # fastapi, httpx, uvicorn
COPY proxy.py .
CMD ["uvicorn", "proxy:app", "--host", "0.0.0.0", "--port", "8080"]
```

ถ้าแก้ `proxy.py` ต้อง `--build` ใหม่เพื่อให้ image อัปเดต

---

## Dockerfile (otel-collector) — ยังไม่ได้ใช้

```dockerfile
FROM otel/opentelemetry-collector-contrib:latest
COPY otel-config.yaml /etc/otel/config.yaml   # bake config ข้างใน image
EXPOSE 4317 4318 9464
CMD ["--config", "/etc/otel/config.yaml"]
```

ตอนนี้ `docker-compose.yml` ใช้ `image:` + volume mount แทน  
ถ้าต้องการ deploy บน server ที่ไม่มี otel-config.yaml (เช่น Railway, Render) ค่อยเปลี่ยนมาใช้ `build: Dockerfile`

---

## Troubleshooting

### ลบ log แล้ว otel-logs.jsonl หายไม่กลับมา

```bash
# อย่าลบไฟล์ขณะ container รัน — ใช้ truncate แทน
> logs/otel-logs.jsonl

# ถ้าลบไปแล้ว ต้อง restart collector ให้สร้างไฟล์ใหม่
docker compose restart otel-collector
# รอ ~10 วินาที ไฟล์จะปรากฏ
```

### proxy ไม่รับ request

```bash
# ตรวจ port 8080
curl http://localhost:8080/v1/models

# ดู error log
docker compose logs proxy --tail=20
```

### otel-collector ไม่รับ telemetry

```bash
# ทดสอบ endpoint
curl -s -X POST http://localhost:4318/v1/logs \
  -H "Content-Type: application/json" \
  -d '{"resourceLogs":[]}' -w "\nHTTP: %{http_code}"
# ต้องได้ HTTP: 200

# ดู error log
docker compose logs otel-collector --tail=20
```

### rebuild หลังแก้ otel-config.yaml

```bash
# otel-collector ใช้ volume mount — แค่ restart พอ ไม่ต้อง rebuild
docker compose restart otel-collector
```
