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

# 2. Docker sanity check. A failing native command does NOT throw in PowerShell
#    (and $PSNativeCommandUseErrorActionPreference does not exist in 5.1), so the
#    previous try/catch could never fire and "Docker is available" printed even
#    when the engine was down. Check the exit code instead.
$null = docker info 2>&1
if ($LASTEXITCODE -ne 0) {
    Fail("Docker Desktop does not appear to be running (docker info exit $LASTEXITCODE). Start it and retry.")
}
Write-Host "[bootstrap] Docker is available." -ForegroundColor Green

# Mythic stores its database/admin credentials in persistent state. Do not
# silently replace legacy credentials when an initialized volume exists.
$projectName = "c2stack"
foreach ($line in (Get-Content .env)) {
    if ($line -match '^COMPOSE_PROJECT_NAME=(.*)$') {
        $projectName = (($Matches[1] -replace '\s+#.*$', '').Trim())
    }
}
$mythicVolume = "${projectName}_mythic_postgres"
$null = docker volume inspect $mythicVolume 2>&1
$mythicVolumeExists = $LASTEXITCODE -eq 0
if ($mythicVolumeExists) {
    $missingMythic = @()
    foreach ($name in @("MYTHIC_ADMIN_PASSWORD", "MYTHIC_DB_PASSWORD", "MYTHIC_RABBITMQ_PASSWORD")) {
        $configured = $false
        foreach ($line in (Get-Content .env)) {
            if ($line -match "^$name=(.+)$" -and $Matches[1].Trim()) { $configured = $true; break }
        }
        if (-not $configured) { $missingMythic += $name }
    }
    if ($missingMythic.Count -gt 0) {
        Fail("An initialized Mythic volume exists but its credentials are not in .env: $($missingMythic -join ', '). Restore matching credentials before starting this stack. Do not reset the volume.")
    }
}

# Create strong deployment-specific credentials in the ignored local .env.
# Existing non-empty values are preserved, and values are never printed.
function Ensure-SecretLine([string]$Name) {
    $lines = [System.Collections.Generic.List[string]]::new()
    foreach ($line in (Get-Content .env)) { $lines.Add($line) }
    $found = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match "^$Name=(.*)$") {
            $found = $true
            if ($Matches[1].Trim()) { return }
            break
        }
    }
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    $value = [BitConverter]::ToString($bytes).Replace("-", "").ToLowerInvariant()
    if ($found) { $lines[$i] = "$Name=$value" } else { $lines.Add("$Name=$value") }
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllLines((Join-Path $ScriptDir ".env"), $lines, $utf8NoBom)
}
Ensure-SecretLine "HAVOC_OPERATOR_PASSWORD"
Ensure-SecretLine "HAVOC_SERVICE_PASSWORD"
Ensure-SecretLine "ADAPTIX_TEAMSERVER_PASSWORD"
Ensure-SecretLine "MYTHIC_ADMIN_PASSWORD"
Ensure-SecretLine "MYTHIC_DB_PASSWORD"
Ensure-SecretLine "MYTHIC_RABBITMQ_PASSWORD"
Ensure-SecretLine "KALI_VNC_PASSWORD"
Ensure-SecretLine "C2STACK_API_KEY"
Write-Host "[bootstrap] Generated per-deployment framework, portal, and desktop credentials in the ignored .env file." -ForegroundColor Green

# Apollo 0.6.14 reads the RabbitMQ password from JSON rather than environment
# variables. Render its ignored runtime config from the same dotenv values.
$envValues = @{}
foreach ($line in (Get-Content .env)) {
    if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
        $envValues[$Matches[1]] = (($Matches[2] -replace '\s+#.*$', '').Trim())
    }
}
$rabbitPassword = [string]$envValues["MYTHIC_RABBITMQ_PASSWORD"]
if (-not $rabbitPassword) { Fail("MYTHIC_RABBITMQ_PASSWORD is required to render Apollo config.") }
$templatePath = Join-Path $ScriptDir "mythic/apollo/rabbitmq_config.template.json"
$generatedPath = Join-Path $ScriptDir "mythic/apollo/rabbitmq_config.generated.json"
$apolloConfig = Get-Content -Raw $templatePath | ConvertFrom-Json
$apolloConfig.rabbitmq_password = $rabbitPassword
$json = $apolloConfig | ConvertTo-Json -Depth 8
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($generatedPath, $json + [Environment]::NewLine, $utf8NoBom)

# 3. Build + up.
$composeArgs = @("compose", "--env-file", ".env")
if ($Mythic -or $All) { $composeArgs += @("--profile", "mythic") }
if ($Adaptix -or $All) { $composeArgs += @("--profile", "adaptix") }
$composeArgs += @("up", "-d")
if (-not $NoBuild) { $composeArgs += "--build" }

Write-Host "[bootstrap] Starting C2Stack stack..." -ForegroundColor Cyan

& docker @composeArgs
if ($LASTEXITCODE -ne 0) { Fail("docker compose up failed (exit $LASTEXITCODE).") }

# The portal's persisted Lab Config volume can outlive .env. Render it after
# deployment credentials exist, then restart its consumers. This preserves
# saved lab settings while preventing stale Havoc credentials from looping.
$portalConfigSync = 'import json,os,urllib.request; req=urllib.request.Request("http://127.0.0.1:8000/api/config/apply", data=b"", method="POST", headers={"X-API-Key":os.environ["C2STACK_API_KEY"]}); result=json.load(urllib.request.urlopen(req, timeout=5)); raise SystemExit(0 if result.get("ok") and not result.get("errors") else 1)'
$configSynced = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    $null = & docker compose --env-file .env exec -T portal python -c $portalConfigSync 2>&1
    if ($LASTEXITCODE -eq 0) { $configSynced = $true; break }
    Start-Sleep -Seconds 2
}
if (-not $configSynced) { Fail("Portal could not apply persisted lab config; refusing to leave config consumers on stale settings.") }
$null = & docker compose --env-file .env restart redirector havoc meridian 2>&1
if ($LASTEXITCODE -ne 0) { Fail("Could not restart config consumers after portal sync (exit $LASTEXITCODE).") }
Write-Host "[bootstrap] Persisted lab config synchronized; redirector, Havoc, and Meridian restarted with current credentials." -ForegroundColor Green

# 4. Parse config for display & status.
$cfg = @{}
if (Test-Path .env) {
    foreach ($rawLine in (Get-Content .env)) {
        $line = $rawLine.Trim()
        if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
            $parts = $line.Split("=", 2)
            # Strip trailing inline comments ("15353  # note") the way Compose
            # does - .env.example uses them, and step 1 copies that file to .env
            # on first run, so ports printed as "15353 # ..." are wrong.
            $value = ($parts[1] -replace '\s+#.*$', '').Trim()
            $cfg[$parts[0].Trim()] = $value
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
  - C2Stack Flight Control UI    : http://localhost:$portalPort (or http://<redirector-host>:$portalPort)
  - Redirector callback endpoint : http://<redirector-host>:$redirectorPort
  - Mythic REST API (if enabled) : http://<redirector-host>:$mythicPort
  - Sliver operator port         : $sliverPort
  - Havoc teamserver port        : $havocPort
  - Adaptix teamserver port      : $adaptixPort  (Qt GUI client)
  - Meridian DNS Listener        : <redirector-host>:$meridianDns/udp (DNS Covert Channel)
  - Meridian HTTP Callback       : http://<redirector-host>:$redirectorPort$meridianPrefix

  Verify the redirector decoy page (no header -> CloudEdge CDN):
    curl http://<redirector-host>:$redirectorPort/

  Verify C2 routing (with header -> backend). Meridian is always enabled, so
  probe it (was hardcoded, ignoring MERIDIAN_URI_PREFIX):
    curl -H "${c2HeaderName}: $c2HeaderVal" `
      http://<redirector-host>:$redirectorPort$meridianPrefix/

  Adaptix operator connection (Qt GUI client on Kali):
    Configure endpoint to <redirector-host>:$adaptixPort

  Meridian console / payload execution (on target):
    Linux:   ./parallax-linux-amd64
    Windows: parallax-windows-amd64.exe (DNS: c2.lab.local or HTTP via redirector)
"@
Write-Host $summary -ForegroundColor White

Pop-Location



