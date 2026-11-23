$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$env:TEMP = Join-Path $taskRoot 'build\tmp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
$taskPython = 'C:\Users\hj0320\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
& $taskPython -m venv "$taskRoot\.venv"
if ($LASTEXITCODE -ne 0) { throw 'venv creation failed' }
& "$taskRoot\.venv\Scripts\python.exe" -m pip install --cache-dir "$taskRoot\dependencies\pip-cache" -r "$taskRoot\requirements.txt"
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
