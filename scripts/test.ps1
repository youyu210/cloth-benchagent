$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
& "$taskRoot\.venv\Scripts\python.exe" -m unittest discover -s tests -v 2>&1 | Tee-Object -FilePath "$taskRoot\docs\unit-tests.log"
if ($LASTEXITCODE -ne 0) { throw 'Unit tests failed' }
