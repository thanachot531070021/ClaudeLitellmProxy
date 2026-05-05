# setup-employee.ps1
# ติดตั้ง Claude Code ให้ผ่าน proxy
# วิธีใช้: .\setup-employee.ps1 -ProxyUrl "http://192.168.1.100:8080" -Email "name@company.com" -Department "IT"

param(
    [Parameter(Mandatory=$true)]
    [string]$ProxyUrl,

    [Parameter(Mandatory=$true)]
    [string]$Email,

    [Parameter(Mandatory=$false)]
    [string]$Department = "general"
)

$ClaudeDir = Join-Path $env:USERPROFILE ".claude"
$SettingsFile = Join-Path $ClaudeDir "settings.json"

Write-Host "=== Claude Code Proxy Setup ===" -ForegroundColor Cyan
Write-Host "Proxy  : $ProxyUrl"
Write-Host "Email  : $Email"
Write-Host "Dept   : $Department"
Write-Host ""

# สร้าง .claude dir ถ้าไม่มี
if (-not (Test-Path $ClaudeDir)) {
    New-Item -ItemType Directory -Path $ClaudeDir | Out-Null
    Write-Host "[OK] Created $ClaudeDir" -ForegroundColor Green
}

# อ่าน settings เดิม (ถ้ามี) แล้ว merge
$settings = @{}
if (Test-Path $SettingsFile) {
    try {
        $raw = Get-Content $SettingsFile -Raw -Encoding UTF8
        $settings = $raw | ConvertFrom-Json
        # แปลง PSCustomObject เป็น hashtable
        $settingsHash = @{}
        $settings.PSObject.Properties | ForEach-Object { $settingsHash[$_.Name] = $_.Value }
        $settings = $settingsHash
        Write-Host "[OK] Loaded existing settings.json" -ForegroundColor Green
    } catch {
        Write-Host "[WARN] Could not parse existing settings.json, will overwrite" -ForegroundColor Yellow
        $settings = @{}
    }
}

# ตั้งค่า env section
$env_config = @{
    ANTHROPIC_BASE_URL                = $ProxyUrl
    CLAUDE_CODE_ENABLE_TELEMETRY      = "1"
    OTEL_METRICS_EXPORTER             = "otlp"
    OTEL_LOGS_EXPORTER                = "otlp"
    OTEL_EXPORTER_OTLP_PROTOCOL       = "http/protobuf"
    OTEL_EXPORTER_OTLP_ENDPOINT       = "http://localhost:4318"
    OTEL_METRIC_EXPORT_INTERVAL       = "10000"
    OTEL_LOGS_EXPORT_INTERVAL         = "5000"
    OTEL_RESOURCE_ATTRIBUTES          = "user.email=$Email,department=$Department,service.name=claude-code"
}

$settings["env"] = $env_config

# เขียน settings.json
$settings | ConvertTo-Json -Depth 10 | Out-File -FilePath $SettingsFile -Encoding UTF8
Write-Host "[OK] Written $SettingsFile" -ForegroundColor Green

# ตั้ง env var ระดับ User ด้วย (สำรอง กันกรณี Claude Code ไม่อ่าน settings)
[System.Environment]::SetEnvironmentVariable("ANTHROPIC_BASE_URL", $ProxyUrl, "User")
Write-Host "[OK] Set ANTHROPIC_BASE_URL in User environment" -ForegroundColor Green

# verify
Write-Host ""
Write-Host "=== Verification ===" -ForegroundColor Cyan
$check = [System.Environment]::GetEnvironmentVariable("ANTHROPIC_BASE_URL", "User")
if ($check -eq $ProxyUrl) {
    Write-Host "[PASS] ANTHROPIC_BASE_URL = $check" -ForegroundColor Green
} else {
    Write-Host "[FAIL] ANTHROPIC_BASE_URL not set correctly" -ForegroundColor Red
}

if (Test-Path $SettingsFile) {
    Write-Host "[PASS] settings.json exists at $SettingsFile" -ForegroundColor Green
} else {
    Write-Host "[FAIL] settings.json not found" -ForegroundColor Red
}

Write-Host ""
Write-Host "Done. Please restart Claude Code for changes to take effect." -ForegroundColor Cyan
