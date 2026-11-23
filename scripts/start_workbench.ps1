param(
    [ValidateRange(1024,65535)]
    [int]$Port = 8514
)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
& "$taskRoot\.venv\Scripts\python.exe" -m streamlit run "$taskRoot\ui.py" --server.address=127.0.0.1 --server.port=$Port --server.fileWatcherType=none --browser.gatherUsageStats=false
if ($LASTEXITCODE -ne 0) { throw '工作台服务已退出，请检查端口与依赖。' }
