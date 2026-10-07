<#
.SYNOPSIS
    Start SignalProof on Windows with Docker Desktop.

.DESCRIPTION
    Creates .env from .env.example, generates the simulated replay dataset on
    first run, builds and starts every service, waits for the API, and opens
    the terminal UI in your browser.

.EXAMPLE
    .\start.ps1             # start (or restart) everything
    .\start.ps1 -Live       # switch to real exchange data and start
    .\start.ps1 -Simulated  # switch back to the simulated dataset and start
    .\start.ps1 -Logs       # start, then follow the logs
    .\start.ps1 -Stop       # stop everything, keep the database
    .\start.ps1 -Reset      # stop and delete the database volume
#>
param(
    [switch]$Stop,
    [switch]$Reset,
    [switch]$Logs,
    [switch]$NoBrowser,
    [switch]$Live,
    [switch]$Simulated,
    [int]$Days = 120
)

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

function Invoke-Compose {
    & docker compose @args
    if ($LASTEXITCODE -ne 0) { throw "docker compose $($args -join ' ') failed (exit $LASTEXITCODE)" }
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw 'Docker was not found. Install Docker Desktop from https://www.docker.com/products/docker-desktop/ and run this again.'
}
# Windows PowerShell 5.1 turns redirected native stderr into a terminating
# error under 'Stop', so relax it just for this probe.
$ErrorActionPreference = 'Continue'
& docker info *> $null
$dockerUp = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = 'Stop'
if (-not $dockerUp) {
    throw 'Docker is installed but not running. Start Docker Desktop, wait for it to say "Engine running", then run this again.'
}

if ($Stop -or $Reset) {
    if ($Reset) { Invoke-Compose down --volumes } else { Invoke-Compose down }
    Write-Host 'SignalProof stopped.'
    return
}

# Bind-mounted code edited on Windows only reloads inside the containers if the
# watchers poll. docker-compose.yml reads this variable.
$env:SP_FORCE_POLLING = 'true'

if (-not (Test-Path '.env')) {
    Copy-Item '.env.example' '.env'
    Write-Host 'Created .env from .env.example.'
}

if ($Live -and $Simulated) { throw 'Pick one of -Live or -Simulated.' }

# The mode is stored in .env, so a later plain .\start.ps1 keeps it.
# .NET file calls keep .env's UTF-8 intact on Windows PowerShell 5.1.
$envPath = Join-Path $PSScriptRoot '.env'
$envText = [IO.File]::ReadAllText($envPath)
if ($Live -or $Simulated) {
    $want = if ($Live) { 'live' } else { 'replay' }
    if ($envText -match '(?m)^SP_MARKET_MODE=') {
        $envText = $envText -replace '(?m)^SP_MARKET_MODE=[^\r\n]*', "SP_MARKET_MODE=$want"
    } else {
        $envText = $envText.TrimEnd() + "`nSP_MARKET_MODE=$want`n"
    }
    [IO.File]::WriteAllText($envPath, $envText, (New-Object Text.UTF8Encoding $false))
}
$mode = 'replay'
if ($envText -match '(?m)^SP_MARKET_MODE=\s*(\w+)') { $mode = $Matches[1].ToLower() }
Write-Host "Market data: $(if ($mode -eq 'live') { 'LIVE exchange feeds' } else { 'simulated replay dataset' })"

if ($mode -ne 'live' -and -not (Test-Path 'backend/app/seed/data/events.jsonl')) {
    Write-Host "Generating the simulated replay dataset ($Days days, about 40 MB)..."
    Invoke-Compose run --rm api python -m app.seed.generate_replay --days $Days --seed 7
}

Write-Host 'Building and starting services (the first build takes a few minutes)...'
Invoke-Compose up -d --build

Write-Host -NoNewline 'Waiting for the API'
$healthy = $false
for ($i = 0; $i -lt 90; $i++) {
    try {
        $r = Invoke-WebRequest -Uri 'http://localhost:8000/health' -UseBasicParsing -TimeoutSec 2
        if ($r.StatusCode -eq 200) { $healthy = $true; break }
    } catch { }
    Write-Host -NoNewline '.'
    Start-Sleep -Seconds 2
}
Write-Host ''
if (-not $healthy) {
    Write-Warning 'The API did not answer within 3 minutes. Check: docker compose logs api'
}

Write-Host ''
Write-Host 'SignalProof is running:'
Write-Host '  Terminal UI    http://localhost:3000'
Write-Host '  API            http://localhost:8000'
Write-Host '  API reference  http://localhost:8000/docs'
Write-Host ''
Write-Host 'Stop it with .\start.ps1 -Stop'

if ($healthy -and -not $NoBrowser) { Start-Process 'http://localhost:3000' }
if ($Logs) { Invoke-Compose logs -f }
