#!/bin/bash
# ===================================================
# สคริปต์สร้าง Virtual Key ให้พนักงานแต่ละคน
# วิธีใช้: bash add_employee_keys.sh
# ===================================================

PROXY_URL="https://YOUR-APP.up.railway.app"   # <-- เปลี่ยนเป็น URL Railway ของพี่
MASTER_KEY="sk-company-master-2025"            # <-- เปลี่ยนให้ตรงกับที่ตั้งใน Railway

# รายชื่อพนักงาน (เพิ่มได้เรื่อยๆ)
declare -A EMPLOYEES=(
  ["somchai"]="สมชาย"
  ["somsri"]="สมศรี"
  ["wanchai"]="วันชัย"
)

echo "กำลังสร้าง Virtual Key สำหรับพนักงาน..."
echo "=================================================="

for username in "${!EMPLOYEES[@]}"; do
  name="${EMPLOYEES[$username]}"
  
  RESPONSE=$(curl -s -X POST "$PROXY_URL/key/generate" \
    -H "Authorization: Bearer $MASTER_KEY" \
    -H "Content-Type: application/json" \
    -d "{
      \"key_alias\": \"$username\",
      \"user_id\": \"$username\",
      \"metadata\": {\"name\": \"$name\"},
      \"max_budget\": 10,
      \"budget_duration\": \"30d\",
      \"models\": [\"claude-sonnet\", \"claude-haiku\", \"claude-opus\"]
    }")
  
  KEY=$(echo $RESPONSE | python3 -c "import sys,json; print(json.load(sys.stdin)['key'])" 2>/dev/null)
  
  if [ -n "$KEY" ]; then
    echo "✓ $name ($username)"
    echo "  Key: $KEY"
    echo ""
  else
    echo "✗ $name ($username) — error: $RESPONSE"
  fi
done

echo "=================================================="
echo "แจก key แต่ละคนให้ตั้ง environment variable บนเครื่องตัวเอง:"
echo ""
echo "  Mac/Linux (~/.zshrc หรือ ~/.bashrc):"
echo "    export ANTHROPIC_BASE_URL=$PROXY_URL"
echo "    export ANTHROPIC_API_KEY=sk-xxxxx-ของ-คุณ"
echo ""
echo "  Windows (System Environment Variables):"
echo "    ANTHROPIC_BASE_URL = $PROXY_URL"
echo "    ANTHROPIC_API_KEY  = sk-xxxxx-ของ-คุณ"
