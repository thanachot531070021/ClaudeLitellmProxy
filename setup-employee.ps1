#Requires -Version 5
# setup-employee.ps1 - Setup Claude Monitor on employee machine (run once)
#
# Usage:
#   .\setup-employee.ps1 -ServerIp "192.168.1.100" -Email "name@company.com"
#
# What it does:
#   1. Set ANTHROPIC_BASE_URL -> proxy:8080  (Claude Code)
#   2. Download + install CA cert from server (Claude Desktop)
#   3. Set system proxy -> server:8081       (Claude Desktop)
#   4. Write Claude Code settings.json

param(
    [Parameter(Mandatory=$true)]
    [string]$ServerIp,

    [Parameter(Mandatory=$true)]
    [string]$Email,

    [Parameter(Mandatory=$false)]
    [string]$Department = "general",

    [Parameter(Mandatory=$false)]
    [switch]$SkipDesktop
)

# Auto-elevate to Admin if not already running as Admin
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]"Administrator")
if (-not $isAdmin) {
    $args = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -ServerIp `"$ServerIp`" -Email `"$Email`" -Department `"$Department`""
    if ($SkipDesktop) { $args += " -SkipDesktop" }
    Start-Process powershell -Verb RunAs -ArgumentList $args
    exit
}

$ProxyUrl     = "http://${ServerIp}:8080"
$CertUrl      = "http://${ServerIp}:8080/cert"
$ClaudeDir    = Join-Path $env:USERPROFILE ".claude"
$SettingsFile = Join-Path $ClaudeDir "settings.json"

Write-Host ""
Write-Host "=== Claude Monitor Setup ===" -ForegroundColor Cyan
Write-Host "  Server : $ServerIp"
Write-Host "  Email  : $Email"
Write-Host "  Dept   : $Department"
Write-Host ""

# -- 1. Claude Code settings --
Write-Host "[1/4] Configure Claude Code..." -ForegroundColor Yellow

if (-not (Test-Path $ClaudeDir)) {
    New-Item -ItemType Directory -Path $ClaudeDir | Out-Null
}

$settings = @{}
if (Test-Path $SettingsFile) {
    try {
        $raw = Get-Content $SettingsFile -Raw -Encoding UTF8
        $obj = $raw | ConvertFrom-Json
        $obj.PSObject.Properties | ForEach-Object { $settings[$_.Name] = $_.Value }
        Write-Host "  [OK] Loaded existing settings.json" -ForegroundColor Green
    } catch {
        Write-Host "  [WARN] Cannot parse existing settings.json - will overwrite" -ForegroundColor Yellow
    }
}

$settings["env"] = @{
    ANTHROPIC_BASE_URL           = $ProxyUrl
    CLAUDE_CODE_ENABLE_TELEMETRY = "1"
    OTEL_METRICS_EXPORTER        = "otlp"
    OTEL_LOGS_EXPORTER           = "otlp"
    OTEL_EXPORTER_OTLP_PROTOCOL  = "http/protobuf"
    OTEL_EXPORTER_OTLP_ENDPOINT  = "http://localhost:4318"
    OTEL_METRIC_EXPORT_INTERVAL  = "10000"
    OTEL_LOGS_EXPORT_INTERVAL    = "5000"
    OTEL_RESOURCE_ATTRIBUTES     = "user.email=$Email,department=$Department,service.name=claude-code"
}

$settings | ConvertTo-Json -Depth 10 | Out-File -FilePath $SettingsFile -Encoding UTF8
[System.Environment]::SetEnvironmentVariable("ANTHROPIC_BASE_URL", $ProxyUrl, "User")
Write-Host "  [OK] settings.json written, ANTHROPIC_BASE_URL set" -ForegroundColor Green

# -- 2. Download + install CA cert --
if (-not $SkipDesktop) {
    Write-Host ""
    Write-Host "[2/4] Download CA Certificate from server..." -ForegroundColor Yellow

    $certPath = Join-Path $env:TEMP "claude-monitor-ca.crt"
    try {
        Invoke-WebRequest -Uri $CertUrl -OutFile $certPath -UseBasicParsing -TimeoutSec 10
        Write-Host "  [OK] Downloaded -> $certPath" -ForegroundColor Green
    } catch {
        Write-Host "  [FAIL] Cannot download cert: $_" -ForegroundColor Red
        Write-Host "         Check that Docker is running and $CertUrl is reachable" -ForegroundColor Red
        exit 1
    }

    Write-Host ""
    Write-Host "[3/4] Install CA Certificate (requires Admin)..." -ForegroundColor Yellow
    try {
        Import-Certificate -FilePath $certPath -CertStoreLocation Cert:\LocalMachine\Root | Out-Null
        Write-Host "  [OK] Certificate installed in Trusted Root CA" -ForegroundColor Green
    } catch {
        Write-Host "  [FAIL] Cannot install cert: $_" -ForegroundColor Red
        Write-Host "         Please run PowerShell as Administrator" -ForegroundColor Red
        exit 1
    }

    # -- 3. Set system proxy --
    Write-Host ""
    Write-Host "[4/4] Set System Proxy for Claude Desktop..." -ForegroundColor Yellow

    $regPath = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    Set-ItemProperty -Path $regPath -Name ProxyEnable   -Value 1
    Set-ItemProperty -Path $regPath -Name ProxyServer   -Value "${ServerIp}:8081"
    Set-ItemProperty -Path $regPath -Name ProxyOverride -Value "localhost;127.0.0.1;<local>"
    Write-Host "  [OK] System proxy -> ${ServerIp}:8081" -ForegroundColor Green

} else {
    Write-Host ""
    Write-Host "[2-4/4] Skipped Claude Desktop setup (-SkipDesktop)" -ForegroundColor DarkGray
}

# -- Verify --
Write-Host ""
Write-Host "=== Verification ===" -ForegroundColor Cyan

$checkUrl = [System.Environment]::GetEnvironmentVariable("ANTHROPIC_BASE_URL", "User")
if ($checkUrl -eq $ProxyUrl) {
    Write-Host "  [PASS] ANTHROPIC_BASE_URL = $checkUrl" -ForegroundColor Green
} else {
    Write-Host "  [FAIL] ANTHROPIC_BASE_URL not set correctly" -ForegroundColor Red
}

if (Test-Path $SettingsFile) {
    Write-Host "  [PASS] settings.json = $SettingsFile" -ForegroundColor Green
} else {
    Write-Host "  [FAIL] settings.json not found" -ForegroundColor Red
}

if (-not $SkipDesktop) {
    $reg = Get-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    if ($reg.ProxyEnable -eq 1 -and $reg.ProxyServer -eq "${ServerIp}:8081") {
        Write-Host "  [PASS] System proxy = $($reg.ProxyServer)" -ForegroundColor Green
    } else {
        Write-Host "  [FAIL] System proxy not set correctly" -ForegroundColor Red
    }

    $cert = Get-ChildItem Cert:\LocalMachine\Root | Where-Object { $_.Subject -like "*mitmproxy*" }
    if ($cert) {
        Write-Host "  [PASS] CA Certificate installed" -ForegroundColor Green
    } else {
        Write-Host "  [WARN] mitmproxy cert not found in Trusted Root" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "Done. Please restart Claude Code and Claude Desktop." -ForegroundColor Cyan
Write-Host ""
