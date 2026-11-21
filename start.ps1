param([switch]$NoInstall, [switch]$NoBrowser, [int]$ApiPort=8000, [int]$WebPort=3000)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$be = Join-Path $root 'backend'
$fe = Join-Path $root 'frontend'
$runtime = Join-Path $root '.validation'
$null = New-Item -ItemType Directory -Force -Path $runtime
foreach ($port in @($ApiPort,$WebPort)) {
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) { throw "Port $port is already in use; choose another port or stop the existing service." }
}
if (-not $NoInstall) {
    Push-Location $be
    try { python -m uv sync --locked --group media --group dev; if ($LASTEXITCODE -ne 0) { throw 'Backend dependency sync failed. Install uv: python -m pip install uv' } } finally { Pop-Location }
    if (-not (Test-Path (Join-Path $fe 'node_modules/next'))) {
        Push-Location $fe
        try { npm ci; if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency install failed' } } finally { Pop-Location }
    }
}
foreach ($tool in @('ffmpeg','ffprobe')) {
    $localTool = Join-Path $root ".tools/ffmpeg/$tool.exe"
    if (Test-Path $localTool) { [Environment]::SetEnvironmentVariable("OMNIMASK_$($tool.ToUpper())",$localTool,'Process') }
    if (-not [Environment]::GetEnvironmentVariable("OMNIMASK_$($tool.ToUpper())") -and -not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "Missing $tool. Install FFmpeg or configure OMNIMASK_$($tool.ToUpper())." }
}
$model = Join-Path $root 'models/faster-whisper-base'
if ((Test-Path (Join-Path $model 'model.bin')) -and -not $env:OMNIMASK_ASR_MODEL) { $env:OMNIMASK_ASR_MODEL = $model }
$env:BACKEND_URL = "http://127.0.0.1:$ApiPort"
$python = Join-Path $be '.venv/Scripts/python.exe'
$node = (Get-Command node).Source
$next = Join-Path $fe 'node_modules/next/dist/bin/next'
$backendArgs = @('-m','uvicorn','app.main:app','--host','127.0.0.1','--port',"$ApiPort")
if (Test-Path (Join-Path $be '.env')) { $backendArgs += @('--env-file',(Join-Path $be '.env')) }
$back = Start-Process -FilePath $python -ArgumentList $backendArgs -WorkingDirectory $be -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtime 'backend.stdout.log') -RedirectStandardError (Join-Path $runtime 'backend.stderr.log') -PassThru
$front = Start-Process -FilePath $node -ArgumentList @($next,'dev','--hostname','127.0.0.1','--port',"$WebPort") -WorkingDirectory $fe -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtime 'frontend.stdout.log') -RedirectStandardError (Join-Path $runtime 'frontend.stderr.log') -PassThru
function ServiceIdentity($proc) {
    $cim = Get-CimInstance Win32_Process -Filter "ProcessId = $($proc.Id)"
    return @{id=$proc.Id;exe=$proc.Path;started=$proc.StartTime.ToUniversalTime().ToString('o');started_utc_ticks=$proc.StartTime.ToUniversalTime().Ticks;command_line=$cim.CommandLine}
}
$record = @{schema_version=2;backend=(ServiceIdentity $back);frontend=(ServiceIdentity $front);apiPort=$ApiPort;webPort=$WebPort}
$record | ConvertTo-Json -Depth 7 | Set-Content (Join-Path $runtime 'processes.json')
$healthy=$false
for ($i=0;$i -lt 60;$i++) {
    try { $null=Invoke-RestMethod "http://127.0.0.1:$ApiPort/api/health"; $healthy=$true; break } catch { Start-Sleep -Milliseconds 250 }
}
if (-not $healthy) { throw 'Backend startup failed. Check .validation/backend.stderr.log and run stop.ps1.' }
$webHealthy=$false
for ($i=0;$i -lt 60;$i++) {
    try { $response=Invoke-WebRequest "http://127.0.0.1:$WebPort" -UseBasicParsing -TimeoutSec 5; if ($response.StatusCode -eq 200) { $webHealthy=$true; break } } catch { }
    Start-Sleep -Milliseconds 250
}
if (-not $webHealthy) { throw 'Frontend startup failed. Check .validation/frontend.stderr.log and run stop.ps1.' }
$processSnapshot = @(Get-CimInstance Win32_Process)
foreach ($role in @('backend','frontend')) {
    $service = $record[$role]
    $family = @($processSnapshot | Where-Object ProcessId -eq $service.id)
    do {
        $added = @($processSnapshot | Where-Object { $_.ParentProcessId -in @($family.ProcessId) -and $_.ProcessId -notin @($family.ProcessId) })
        $family += $added
    } while ($added.Count -gt 0)
    $service.processes = @($family | Where-Object { $_.ExecutablePath -ine "$env:SystemRoot\System32\conhost.exe" } | ForEach-Object {
        $p = Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue
        if ($p) { ServiceIdentity $p }
    })
}
$record | ConvertTo-Json -Depth 7 | Set-Content (Join-Path $runtime 'processes.json')
Write-Output "OmniMask http://127.0.0.1:$WebPort (API $ApiPort); logs: $runtime; stop with ./stop.ps1"
if (-not $NoBrowser) { Start-Process "http://127.0.0.1:$WebPort" }
