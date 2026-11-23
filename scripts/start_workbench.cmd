@echo off
setlocal
cd /d "%~dp0.." || exit /b 1

powershell.exe -NoProfile -NonInteractive -Command ^
    "$listeners = @(Get-NetTCPConnection -LocalPort 8514 -State Listen -ErrorAction SilentlyContinue | Where-Object LocalAddress -in '127.0.0.1','0.0.0.0','::');" ^
    "if (!$listeners) { exit 10 }" ^
    "$owners = @($listeners.OwningProcess | Sort-Object -Unique);" ^
    "if ($owners.Count -eq 1) {" ^
    "    $server = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $owners[0]);" ^
    "    $parent = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $server.ParentProcessId);" ^
    "    $python = Join-Path (Get-Location) '.venv\Scripts\python.exe';" ^
    "    $entry = [regex]::Escape((Join-Path (Get-Location) 'ui.py'));" ^
    "    $pattern = 'streamlit run\s+\x22?(?:ui\.py|' + $entry + ')(?:\x22|\s|$)';" ^
    "    if (($server.ExecutablePath -eq $python -or $parent.ExecutablePath -eq $python) -and $server.CommandLine -match $pattern) {" ^
    "        try {" ^
    "            $health = Invoke-WebRequest -Uri 'http://127.0.0.1:8514/_stcore/health' -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop;" ^
    "            if ($health.StatusCode -eq 200 -and $health.Content -eq 'ok') {" ^
    "                Write-Output 'BenchAgent is already running. Open http://127.0.0.1:8514/'; exit 0" ^
    "            }" ^
    "        } catch {}" ^
    "        [Console]::Error.WriteLine('BenchAgent is listening on 8514 but did not pass its health check.'); exit 1" ^
    "    }" ^
    "}" ^
    "[Console]::Error.WriteLine('Port 8514 is already in use. Listening PID(s): ' + ($owners -join ', ')); exit 1"
if "%errorlevel%"=="10" goto launch
exit /b %errorlevel%

:launch
".venv\Scripts\python.exe" -m streamlit run "%CD%\ui.py" --server.address=127.0.0.1 --server.port=8514 --server.headless=true --server.fileWatcherType=none --browser.gatherUsageStats=false
exit /b %errorlevel%
