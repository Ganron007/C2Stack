$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("c2stack-bootstrap-" + [guid]::NewGuid())
$dockerDir = Join-Path $tempRoot "Docker"
$binDir = Join-Path $tempRoot "bin"
New-Item -ItemType Directory -Path $dockerDir, $binDir | Out-Null

try {
    Copy-Item (Join-Path $repoRoot "Docker\docker-bootstrap.ps1") (Join-Path $dockerDir "docker-bootstrap.ps1")
    Copy-Item (Join-Path $repoRoot "Docker\.env.example") (Join-Path $dockerDir ".env.example")
    New-Item -ItemType Directory -Path (Join-Path $dockerDir "mythic\apollo") -Force | Out-Null
    Copy-Item (Join-Path $repoRoot "Docker\mythic\apollo\rabbitmq_config.template.json") `
        (Join-Path $dockerDir "mythic\apollo\rabbitmq_config.template.json")

    $dockerCommand = Join-Path $binDir "docker.cmd"
    @'
@echo off
echo %*>>"%DOCKER_LOG%"
if "%1 %2 %3"=="volume inspect c2stack_mythic_postgres" (
  if "%DOCKER_VOLUME_EXISTS%"=="1" exit /b 0
  exit /b 1
)
exit /b 0
'@ | Set-Content -LiteralPath $dockerCommand -Encoding ascii

    $logPath = Join-Path $tempRoot "docker.log"
    $stdoutPath = Join-Path $tempRoot "stdout.txt"
    $savedPath = $env:PATH
    $savedLog = $env:DOCKER_LOG
    $savedVolumeExists = $env:DOCKER_VOLUME_EXISTS
    try {
        $env:PATH = "$binDir;$savedPath"
        $env:DOCKER_LOG = $logPath
        $env:DOCKER_VOLUME_EXISTS = "0"
        & pwsh -NoProfile -File (Join-Path $dockerDir "docker-bootstrap.ps1") -All -NoBuild *> $stdoutPath
        if ($LASTEXITCODE -ne 0) { throw "bootstrap exited with $LASTEXITCODE" }
    }
    finally {
        $env:PATH = $savedPath
        if ($null -eq $savedLog) { Remove-Item Env:DOCKER_LOG -ErrorAction SilentlyContinue }
        else { $env:DOCKER_LOG = $savedLog }
        if ($null -eq $savedVolumeExists) { Remove-Item Env:DOCKER_VOLUME_EXISTS -ErrorAction SilentlyContinue }
        else { $env:DOCKER_VOLUME_EXISTS = $savedVolumeExists }
    }

    $calls = Get-Content -LiteralPath $logPath
    if ($calls -notcontains "info") { throw "docker info was not checked" }
    if ($calls -notcontains "compose --env-file .env --profile mythic --profile adaptix up -d") {
        throw "-All did not enable both profiles or -NoBuild was lost: $calls"
    }
    if (-not ($calls | Where-Object { $_ -like "compose --env-file .env exec -T portal python -c *" })) {
        throw "portal lab configuration was not synchronized after startup"
    }
    if ($calls -notcontains "compose --env-file .env restart redirector havoc meridian") {
        throw "rendered-config consumers were not restarted after synchronization"
    }
    if ($calls -notcontains "compose --env-file .env ps") { throw "compose status was not queried" }
    if (($calls -join "`n") -match "--build") { throw "-NoBuild unexpectedly passed --build" }

    $envLines = Get-Content -LiteralPath (Join-Path $dockerDir ".env")
    $secretNames = @("HAVOC_OPERATOR_PASSWORD", "HAVOC_SERVICE_PASSWORD", "ADAPTIX_TEAMSERVER_PASSWORD",
        "MYTHIC_ADMIN_PASSWORD", "MYTHIC_DB_PASSWORD", "MYTHIC_RABBITMQ_PASSWORD", "KALI_VNC_PASSWORD",
        "C2STACK_API_KEY")
    foreach ($name in $secretNames) {
        if (-not ($envLines | Where-Object { $_ -match "^$name=[0-9a-f]{64}$" })) {
            throw "$name was not generated into the local .env"
        }
    }
    $rabbitPassword = [string](($envLines | Where-Object { $_ -match '^MYTHIC_RABBITMQ_PASSWORD=' } | Select-Object -First 1) -replace '^MYTHIC_RABBITMQ_PASSWORD=', '')
    $generatedConfig = Get-Content -Raw (Join-Path $dockerDir "mythic\apollo\rabbitmq_config.generated.json") | ConvertFrom-Json
    if ($generatedConfig.rabbitmq_password -ne $rabbitPassword) {
        throw "Apollo runtime config does not use the generated RabbitMQ password"
    }
    $stdout = Get-Content -LiteralPath $stdoutPath -Raw
    if ($stdout -match "(?m)^($($secretNames -join '|'))=[0-9a-f]{64}$") {
        throw "generated password was printed to stdout"
    }
    if ($stdout.Contains($rabbitPassword)) { throw "RabbitMQ password was printed to stdout" }

    $envPath = Join-Path $dockerDir ".env"
    $remaining = Get-Content -LiteralPath $envPath | Where-Object {
        $_ -notmatch '^MYTHIC_(ADMIN|DB|RABBITMQ)_PASSWORD='
    }
    Set-Content -LiteralPath $envPath -Value $remaining -Encoding utf8NoBOM
    $currentPath = $env:PATH
    $currentLog = $env:DOCKER_LOG
    $currentVolumeExists = $env:DOCKER_VOLUME_EXISTS
    try {
    $env:PATH = "$binDir;$savedPath"
    $env:DOCKER_LOG = $logPath
    $env:DOCKER_VOLUME_EXISTS = "1"
    & pwsh -NoProfile -File (Join-Path $dockerDir "docker-bootstrap.ps1") -Mythic -NoBuild *> $stdoutPath
    if ($LASTEXITCODE -eq 0) { throw "bootstrap should refuse an initialized Mythic volume with missing credentials" }
    $legacyOutput = Get-Content -LiteralPath $stdoutPath -Raw
    if ($legacyOutput -notmatch "An initialized Mythic volume exists") {
        throw "legacy volume failure did not explain the credential recovery path"
    }
    if (Get-Content -LiteralPath $envPath | Where-Object { $_ -match '^MYTHIC_(ADMIN|DB|RABBITMQ)_PASSWORD=.+' }) {
        throw "bootstrap generated replacement credentials for an existing Mythic volume"
    }
    }
    finally {
        $env:PATH = $currentPath
        if ($null -eq $currentLog) { Remove-Item Env:DOCKER_LOG -ErrorAction SilentlyContinue }
        else { $env:DOCKER_LOG = $currentLog }
        if ($null -eq $currentVolumeExists) { Remove-Item Env:DOCKER_VOLUME_EXISTS -ErrorAction SilentlyContinue }
        else { $env:DOCKER_VOLUME_EXISTS = $currentVolumeExists }
    }
    Write-Output "PowerShell bootstrap argument and secret checks passed"
}
finally {
    Remove-Item -LiteralPath $tempRoot -Recurse -Force
}
