#Requires -Version 7.0
<#
.SYNOPSIS
    C2Stack Docker practice-lab bootstrap for the local (Windows) Docker host.
.DESCRIPTION
    - Copies .env.example to .env if missing.
    - Verifies Docker Desktop is running.
    - Builds and starts the redirector + Sliver + Havoc (and Mythic with -Mythic).
    - Prints status and operator next steps.
.PARAMETER Mythic
    Also bring up the Mythic stack (needs first-run network access to pull images).
.PARAMETER Adaptix
    Also bring up the Adaptix stack (builds server + extenders from source).
.PARAMETER All
    Bring up all frameworks (mythic + adaptix profiles).
.PARAMETER NoBuild
    Skip the build step (use cached images).
#>
[CmdletBinding()]
param(
    [switch]$Mythic,
    [switch]$Adaptix,
    [switch]$All,
    [switch]$NoBuild
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Push-Location $ScriptDir

function Fail($msg) {
    Write-Error $msg
    exit 1
}

# 1. Ensure .env exists.
if (-not (Test-Path .env)) {
    if (-not (Test-Path .env.example)) { Fail(".env.example missing; cannot continue.") }
    Copy-Item .env.example .env
    Write-Host "[bootstrap] Created .env from .env.example — review it before production use." -ForegroundColor Yellow
}

# 2. Docker sanity check.
try {
    $null = docker info 2>&1
} catch {
    Fail("Docker Desktop does not appear to be running. Start it and retry.")
}
Write-Host "[bootstrap] Docker is available." -ForegroundColor Green

# 3. Build + up.
$composeArgs = @("compose", "--env-file", ".env", "up", "-d")
if (-not $NoBuild) { $composeArgs += "--build" }
if ($Mythic -or $All) { $composeArgs += "--profile", "mythic" }
if ($Adaptix -or $All) { $composeArgs += "--profile", "adaptix" }

Write-Host "[bootstrap] Starting C2Stack stack..." -ForegroundColor Cyan
& docker @composeArgs
if ($LASTEXITCODE -ne 0) { Fail("docker compose up failed (exit $LASTEXITCODE).") }

# 4. Status.
Write-Host "`n[bootstrap] Stack status:" -ForegroundColor Cyan
& docker compose --env-file .env ps

Write-Host @"

[bootstrap] Next steps for the operator (Kali VM):
  - Redirector callback endpoint : http://<host-ip-on-vmnet2>:${env:REDIRECTOR_HTTP_PORT:-80}
  - Mythic UI (if enabled)       : https://<host-ip-on-vmnet2>:${env:MYTHIC_UI_PORT:-7443}
  - Sliver operator port         : ${env:SLIVER_CTRL_PORT:-31337}
  - Havoc teamserver port        : ${env:HAVOC_TS_PORT:-40056}
  - Adaptix teamserver port      : ${env:ADAPTIX_TS_PORT:-4321}  (Qt GUI client)
  - Meridian DNS Listener        : <host-ip-on-vmnet2>:${env:MERIDIAN_DNS_PORT:-5353}/udp (DNS Covert Channel)
  - Meridian HTTP Callback       : http://<host-ip-on-vmnet2>:${env:REDIRECTOR_HTTP_PORT:-80}${env:MERIDIAN_URI_PREFIX:-/gateway/v1/telemetry}

  Verify the redirector decoy page (no header -> CloudEdge CDN):
    curl http://<host-ip-on-vmnet2>:${env:REDIRECTOR_HTTP_PORT:-80}/

  Verify C2 routing (with header -> backend):
    curl -H "${env:C2_HEADER_NAME:-X-Request-ID}: ${env:C2_HEADER_VALUE:-cadre-c2}" `
      http://<host-ip-on-vmnet2>:${env:REDIRECTOR_HTTP_PORT:-80}${env:MYTHIC_URI_PREFIX:-/cdn/media/stream}/

  Adaptix operator connection (Qt GUI client on Kali):
    Configure endpoint to <host-ip-on-vmnet2>:${env:ADAPTIX_TS_PORT:-4321}

  Meridian console / payload execution (on target):
    Linux:   ./parallax-linux-amd64
    Windows: parallax-windows-amd64.exe (DNS: c2.cadre.local or HTTP via redirector)
"@ -ForegroundColor White

Pop-Location
