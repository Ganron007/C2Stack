#Requires -Version 5.1
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
    Write-Host "[bootstrap] Created .env from .env.example - review it before production use." -ForegroundColor Yellow
}


# 2. Docker sanity check.
try {
    $null = docker info 2>&1
} catch {
    Fail("Docker Desktop does not appear to be running. Start it and retry.")
}
Write-Host "[bootstrap] Docker is available." -ForegroundColor Green

# 3. Build + up.
$composeArgs = @("compose", "--env-file", ".env")
if ($Mythic -or $All) { $composeArgs += @("--profile", "mythic") }
if ($Adaptix -or $All) { $composeArgs += @("--profile", "adaptix") }
$composeArgs += @("up", "-d")
if (-not $NoBuild) { $composeArgs += "--build" }

Write-Host "[bootstrap] Starting C2Stack stack..." -ForegroundColor Cyan

& docker @composeArgs
if ($LASTEXITCODE -ne 0) { Fail("docker compose up failed (exit $LASTEXITCODE).") }

# 4. Parse config for display & status.
$cfg = @{}
if (Test-Path .env) {
    foreach ($rawLine in (Get-Content .env)) {
        $line = $rawLine.Trim()
        if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
            $parts = $line.Split("=", 2)
            $cfg[$parts[0].Trim()] = $parts[1].Trim()
        }
    }
}
$portalPort     = "8000"
$redirectorPort = "80"
$sliverPort     = "31337"
$havocPort      = "40056"
$adaptixPort    = "4321"
$mythicPort     = "7443"
$meridianDns    = "15353"
$meridianPrefix = "/gateway/v1/telemetry"
$c2HeaderName   = "X-Request-ID"
$c2HeaderVal    = "cadre-c2"

if ($cfg.ContainsKey("PORTAL_PORT") -and $cfg["PORTAL_PORT"]) { $portalPort = $cfg["PORTAL_PORT"] }
if ($cfg.ContainsKey("REDIRECTOR_HTTP_PORT") -and $cfg["REDIRECTOR_HTTP_PORT"]) { $redirectorPort = $cfg["REDIRECTOR_HTTP_PORT"] }
if ($cfg.ContainsKey("SLIVER_CTRL_PORT") -and $cfg["SLIVER_CTRL_PORT"]) { $sliverPort = $cfg["SLIVER_CTRL_PORT"] }
if ($cfg.ContainsKey("HAVOC_TS_PORT") -and $cfg["HAVOC_TS_PORT"]) { $havocPort = $cfg["HAVOC_TS_PORT"] }
if ($cfg.ContainsKey("ADAPTIX_TS_PORT") -and $cfg["ADAPTIX_TS_PORT"]) { $adaptixPort = $cfg["ADAPTIX_TS_PORT"] }
if ($cfg.ContainsKey("MYTHIC_UI_PORT") -and $cfg["MYTHIC_UI_PORT"]) { $mythicPort = $cfg["MYTHIC_UI_PORT"] }
if ($cfg.ContainsKey("MERIDIAN_DNS_PORT") -and $cfg["MERIDIAN_DNS_PORT"]) { $meridianDns = $cfg["MERIDIAN_DNS_PORT"] }
if ($cfg.ContainsKey("MERIDIAN_URI_PREFIX") -and $cfg["MERIDIAN_URI_PREFIX"]) { $meridianPrefix = $cfg["MERIDIAN_URI_PREFIX"] }
if ($cfg.ContainsKey("C2_HEADER_NAME") -and $cfg["C2_HEADER_NAME"]) { $c2HeaderName = $cfg["C2_HEADER_NAME"] }
if ($cfg.ContainsKey("C2_HEADER_VALUE") -and $cfg["C2_HEADER_VALUE"]) { $c2HeaderVal = $cfg["C2_HEADER_VALUE"] }

Write-Host "`n[bootstrap] Stack status:" -ForegroundColor Cyan
& docker compose --env-file .env ps

$summary = @"

[bootstrap] Next steps for the operator:
  - C2Stack Flight Control UI    : http://localhost:$portalPort (or http://<host-ip-on-vmnet2>:$portalPort)
  - Redirector callback endpoint : http://<host-ip-on-vmnet2>:$redirectorPort
  - Mythic UI (if enabled)       : https://<host-ip-on-vmnet2>:$mythicPort
  - Sliver operator port         : $sliverPort
  - Havoc teamserver port        : $havocPort
  - Adaptix teamserver port      : $adaptixPort  (Qt GUI client)
  - Meridian DNS Listener        : <host-ip-on-vmnet2>:$meridianDns/udp (DNS Covert Channel)
  - Meridian HTTP Callback       : http://<host-ip-on-vmnet2>:$redirectorPort$meridianPrefix

  Verify the redirector decoy page (no header -> CloudEdge CDN):
    curl http://<host-ip-on-vmnet2>:$redirectorPort/

  Verify C2 routing (with header -> backend):
    curl -H "${c2HeaderName}: $c2HeaderVal" `
      http://<host-ip-on-vmnet2>:$redirectorPort/gateway/v1/telemetry/

  Adaptix operator connection (Qt GUI client on Kali):
    Configure endpoint to <host-ip-on-vmnet2>:$adaptixPort

  Meridian console / payload execution (on target):
    Linux:   ./parallax-linux-amd64
    Windows: parallax-windows-amd64.exe (DNS: c2.cadre.local or HTTP via redirector)
"@
Write-Host $summary -ForegroundColor White

Pop-Location



