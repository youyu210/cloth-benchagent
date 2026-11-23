import os
import re
import subprocess
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchagent.common import ROOT, write
dumpbin = r"D:\tools\MicrosoftVisualStudio\2022\BuildTools\VC\Tools\MSVC\14.44.35207\bin\Hostx64\x64\dumpbin.exe"
records, missing = {}, []
for binary in sorted((ROOT/"build/bin").iterdir()):
    if binary.suffix.lower() not in (".exe", ".dll"):
        continue
    output = subprocess.check_output([dumpbin, "/DEPENDENTS", str(binary)], text=True, errors="replace")
    dependencies = re.findall(r"^\s+([\w.-]+\.dll)\s*$", output, re.M | re.I)
    records[binary.name] = {}
    for name in dependencies:
        if name.lower().startswith(("api-ms-win-", "ext-ms-win-")):
            resolved = "Windows API set"
        elif (ROOT/"build/bin"/name).exists():
            resolved = str(ROOT/"build/bin"/name)
        elif (Path(os.environ["SystemRoot"])/"System32"/name).exists():
            resolved = str(Path(os.environ["SystemRoot"])/"System32"/name)
        else:
            resolved = None
            missing.append(name)
        records[binary.name][name] = resolved
write(ROOT/"docs/native_dependency_check.json", {"method":"static PE import inspection; no program execution",
      "records":records,"missing":missing,"passed":not missing})
print("Native dependency inspection:", "PASS" if not missing else missing)
raise SystemExit(1 if missing else 0)
