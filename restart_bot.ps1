# restart_bot.ps1
# Kill the old bot process tree (Python + parent cmd from start-all.bat)
# and start a fresh bot. Does NOT touch NapCat or WebUI.
# Usage: powershell -ExecutionPolicy Bypass -File restart_bot.ps1

$ErrorActionPreference = "Stop"

$botDir       = "D:\nahida-bot-v2\nahida-bot-2"
$configFile   = Join-Path $botDir "config.yaml"
$logFile      = Join-Path $botDir "data\logs\nahida.log"
$startCmd     = "cd /d `"$botDir`" && chcp 65001 >nul && uv run nahida-bot start --config-yaml config.yaml"
$markerCmd    = "Nahida-Bot-Backend"

# The bot python can show up as `python -m nahida_bot ...` or, as installed
# here, as `...\Scripts\nahida-bot.exe start ...` (note the hyphen). Matching
# only "*nahida_bot*" misses the latter, which leaves the old instance alive
# and produces two bots answering every message.
function Test-BotCmdLine {
    param([string]$CmdLine)
    if (-not $CmdLine) { return $false }
    return ($CmdLine -like "*nahida_bot*") -or
           ($CmdLine -like "*nahida-bot.exe*") -or
           ($CmdLine -like "*uv run nahida-bot*")
}

Write-Host "=== restart_bot.ps1 ===" -ForegroundColor Cyan
Write-Host ("Bot dir:        {0}" -f $botDir)
Write-Host ("Config file:    {0}" -f $configFile)
Write-Host ("Log file:       {0}" -f $logFile)
Write-Host ""

# 1. Read current config value for max_triggers_per_hour
$cfgBefore = Select-String -Path $configFile -Pattern "max_triggers_per_hour\s*:\s*(\d+)" | ForEach-Object { $_.Matches[0].Groups[1].Value }
Write-Host ("[1/4] Current config: max_triggers_per_hour = {0}" -f $cfgBefore)

# 2. Find the bot Python process (child of the Nahida-Bot-Backend cmd)
$botPids = @()
Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
    ForEach-Object {
        $cmdline = $_.CommandLine
        if (Test-BotCmdLine $cmdline) {
            $script:botPids += $_.ProcessId
        }
    }
Write-Host ("[2/4] Found {0} bot python pid(s): {1}" -f $botPids.Count, ($botPids -join ", "))

# 3. Find the parent cmd process (started by start-all.bat with title Nahida-Bot-Backend)
$cmdPids = @()
Get-CimInstance Win32_Process -Filter "Name = 'cmd.exe'" |
    ForEach-Object {
        $cmdline = $_.CommandLine
        if ($cmdline -like "*$markerCmd*" -or ($cmdline -like "*$botDir*" -and $cmdline -like "*uv run*")) {
            $script:cmdPids += $_.ProcessId
        }
    }
Write-Host ("[3/4] Found {0} bot-cmd pid(s): {1}" -f $cmdPids.Count, ($cmdPids -join ", "))

# 4. Kill them (Python first, then parent cmd)
$killed = @()
foreach ($p in $botPids) {
    try {
        Stop-Process -Id $p -Force -ErrorAction Stop
        $killed += "python=$p"
    } catch {
        Write-Warning ("Could not kill python pid {0}: {1}" -f $p, $_.Exception.Message)
    }
}
foreach ($p in $cmdPids) {
    try {
        Stop-Process -Id $p -Force -ErrorAction Stop
        $killed += "cmd=$p"
    } catch {
        Write-Warning ("Could not kill cmd pid {0}: {1}" -f $p, $_.Exception.Message)
    }
}
Write-Host ("Killed: {0}" -f ($killed -join ", "))

# 5. Wait a moment for processes to actually exit
Start-Sleep -Seconds 1

# 6. Record log size BEFORE restart so we can detect the new instance
$logSizeBefore = (Get-Item $logFile -ErrorAction SilentlyContinue).Length
Write-Host ("Log size before: {0} bytes" -f $logSizeBefore)

# 7. Start a fresh cmd window running the bot
$args = '/k', $startCmd
Start-Process -FilePath "cmd.exe" -ArgumentList $args -WindowStyle Normal
Write-Host "[4/4] Started new bot cmd window"

# 8. Wait for the new bot to log its first lines
$maxWait = 20
$elapsed = 0
$newPid = $null
while ($elapsed -lt $maxWait) {
    Start-Sleep -Seconds 1
    $elapsed++
    # NOTE: the bot rotates its log (nahida.log -> nahida.log.1) on start, so the
    # new log file can be SMALLER than the old one. Detect the new instance by
    # process id (not by log size), and treat log growth as a secondary hint only.
    $newBot = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
        Where-Object { (Test-BotCmdLine $_.CommandLine) -and ($killed -notcontains "python=$($_.ProcessId)") } |
        Select-Object -First 1
    if ($newBot) {
        $newPid = $newBot.ProcessId
        break
    }
    $logSizeNow = (Get-Item $logFile -ErrorAction SilentlyContinue).Length
    if ($logSizeNow -ne $null -and $logSizeBefore -ne $null -and $logSizeNow -gt $logSizeBefore + 100) {
        Write-Host ("    (log grew from {0} to {1} bytes)" -f $logSizeBefore, $logSizeNow)
    }
}

if ($newPid) {
    $startTime = (Get-Process -Id $newPid).StartTime
    Write-Host ""
    Write-Host "=== restart OK ===" -ForegroundColor Green
    Write-Host ("New bot python pid: {0}" -f $newPid)
    Write-Host ("Started at:         {0}" -f $startTime)
    Write-Host ("Waited:             {0}s" -f $elapsed)
} else {
    Write-Host ""
    Write-Host "=== restart FAILED (no new pid detected within ${maxWait}s) ===" -ForegroundColor Red
    Write-Host "Check the new cmd window for errors."
    exit 1
}
