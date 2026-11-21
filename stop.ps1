$ErrorActionPreference = 'Stop'
$recordPath = Join-Path $PSScriptRoot '.validation/processes.json'
if (-not (Test-Path -LiteralPath $recordPath)) { Write-Output 'No recorded OmniMask processes.'; return }
$recordText = Get-Content -LiteralPath $recordPath -Raw
$items = $recordText | ConvertFrom-Json
$snapshot = @(Get-CimInstance Win32_Process)
$targets = @()

function Get-ProcessIdentity([int]$TaskId) {
    $proc = Get-Process -Id $TaskId -ErrorAction SilentlyContinue
    if (-not $proc) { return $null }
    try {
        $cim = Get-CimInstance Win32_Process -Filter "ProcessId = $TaskId"
        if (-not $cim) { return $null }
        return [pscustomobject]@{ id=$TaskId; exe=$proc.Path; started_utc_ticks=$proc.StartTime.ToUniversalTime().Ticks; command_line=$cim.CommandLine }
    } catch {
        if (-not (Get-Process -Id $TaskId -ErrorAction SilentlyContinue)) { return $null }
        throw
    }
}

foreach ($role in @('backend','frontend')) {
    $service = $items.$role
    if (-not $service) { continue }
    $root = Get-ProcessIdentity ([int]$service.id)
    if ($root) {
        if ($service.started_utc_ticks) {
            $expectedTicks = [long]$service.started_utc_ticks
        } else {
            # Read literal ISO time: Windows PowerShell and PS7 deserialize dates differently.
            $literal = [regex]::Match($recordText, '"'+$role+'"\s*:\s*\{[^}]*"started"\s*:\s*"([^"]+)"').Groups[1].Value
            if (-not $literal) { throw "Missing recorded $role creation time; no processes stopped." }
            $expectedTicks = ([DateTimeOffset]::Parse($literal)).UtcDateTime.Ticks
        }
        if ($root.exe -ne $service.exe -or [Math]::Abs([long]$root.started_utc_ticks-$expectedTicks) -ge 20000000) {
            throw "Recorded $role process identity changed; no processes stopped."
        }
        if ($root.command_line.IndexOf($PSScriptRoot,[StringComparison]::OrdinalIgnoreCase) -lt 0) { throw "Recorded $role command is outside this workspace." }
        $family = @($snapshot | Where-Object ProcessId -eq $service.id)
        do {
            $added = @($snapshot | Where-Object { $_.ParentProcessId -in @($family.ProcessId) -and $_.ProcessId -notin @($family.ProcessId) })
            $family += $added
        } while ($added.Count -gt 0)
        foreach ($member in $family) {
            if ($member.ExecutablePath -ieq "$env:SystemRoot\System32\conhost.exe") { continue }
            $identity = Get-ProcessIdentity ([int]$member.ProcessId)
            if ($identity) { $targets += $identity }
        }
    } else {
        # If a launcher exited first, only its recorded exact child identities qualify.
        foreach ($saved in @($service.processes)) {
            if (-not $saved) { continue }
            $identity = Get-ProcessIdentity ([int]$saved.id)
            if ($identity -and $identity.exe -eq $saved.exe -and
                [long]$identity.started_utc_ticks -eq [long]$saved.started_utc_ticks -and
                $identity.command_line -eq $saved.command_line) { $targets += $identity }
        }
    }
}

$seen = @{}
# Keep roots before children so a supervisor cannot restart a stopped child.
$targets = @($targets | Where-Object {
    if ($seen.ContainsKey([int]$_.id)) { $false } else { $seen[[int]$_.id]=$true; $true }
})
$evidence = @{ checked_at=[DateTime]::UtcNow.ToString('o'); processes=$targets; stopped=$false }
$evidencePath = Join-Path $PSScriptRoot '.validation/stop-last.json'
$evidence | ConvertTo-Json -Depth 7 | Set-Content -LiteralPath $evidencePath -Encoding UTF8
foreach ($target in $targets) {
    $current = Get-ProcessIdentity ([int]$target.id)
    if ($current -and ($current.exe -ne $target.exe -or
        [long]$current.started_utc_ticks -ne [long]$target.started_utc_ticks -or
        $current.command_line -ne $target.command_line)) { throw 'Service identity changed; no processes stopped.' }
}
foreach ($target in $targets) {
    $current = Get-ProcessIdentity ([int]$target.id)
    if (-not $current) { continue }
    if ($current.exe -ne $target.exe -or [long]$current.started_utc_ticks -ne [long]$target.started_utc_ticks -or
        $current.command_line -ne $target.command_line) { throw 'Service identity changed while stopping.' }
    $proc = Get-Process -Id $target.id -ErrorAction SilentlyContinue
    if ($proc) {
        try { Stop-Process -Id $target.id -ErrorAction Stop } catch {
            if (Get-Process -Id $target.id -ErrorAction SilentlyContinue) { throw }
        }
        try { $null = $proc.WaitForExit(5000) } catch { }
    }
}
$remaining = @(Get-NetTCPConnection -LocalPort @($items.apiPort,$items.webPort) -State Listen -ErrorAction SilentlyContinue)
if ($remaining.Count -gt 0) { throw 'A service port still has a listener; no unrelated processes will be terminated. Inspect .validation/stop-last.json.' }
$evidence.stopped = $true
$evidence | ConvertTo-Json -Depth 7 | Set-Content -LiteralPath $evidencePath -Encoding UTF8
Write-Output 'Recorded OmniMask service processes and verified descendants stopped; both ports are free.'
