import shutil
import subprocess
from pathlib import Path
from .common import ROOT, IMPLEMENTATION, read, sha

def inspect():
    errors = []
    backend = read(ROOT / "versions/backend_manifest.json")
    for name, digest in backend["files"].items():
        if sha(ROOT / name) != digest:
            errors.append("Fixed backend configuration changed: " + name)
    source = read(ROOT / "versions/source_manifest.json")
    for name, digest in source["files"].items():
        p = ROOT / "simulator" / name
        if not p.is_file() or sha(p) != digest:
            errors.append("Source/assets changed: " + name)
    runtime_path = ROOT / "versions/runtime_manifest.json"
    runtime = read(runtime_path) if runtime_path.exists() else {}
    if runtime.get("implementation_commit") != IMPLEMENTATION:
        errors.append("Missing or wrong runtime manifest; build first")
    for name, digest in runtime.get("files", {}).items():
        p = ROOT / "build" / "bin" / name
        if not p.is_file() or sha(p) != digest:
            errors.append("Runtime changed/missing: " + name)
    if "gipc.exe" not in runtime.get("files", {}):
        errors.append("No sealed gipc.exe")
    if runtime.get("source_manifest_sha256") != sha(ROOT / "versions/source_manifest.json"):
        errors.append("Runtime was not built for current source manifest")
    gpu = None
    smi = shutil.which("nvidia-smi")
    if smi:
        try:
            p = subprocess.run([smi, "--query-gpu=index,uuid,name,driver_version,memory.total",
                                "--format=csv,noheader"], capture_output=True, text=True, timeout=15)
            if p.returncode != 0 or not p.stdout.strip():
                errors.append("GPU query failed")
            else:
                gpu = p.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            errors.append("GPU query failed")
    else:
        errors.append("nvidia-smi not found")
    return {"ready": not errors, "errors": errors, "gpu": gpu,
            "implementation_commit": IMPLEMENTATION, "runtime": runtime}

def identity():
    manifest = read(ROOT / "versions/runtime_manifest.json")
    files = {name: sha(ROOT / "build/bin" / name) for name in manifest["files"]}
    source = read(ROOT / "versions/source_manifest.json")
    inputs = {name: sha(ROOT / "simulator" / name) for name in source["files"]}
    return {"runtime": files, "source": inputs,
            "source_manifest": sha(ROOT / "versions/source_manifest.json"),
            "runtime_manifest": sha(ROOT / "versions/runtime_manifest.json"),
            "profile": sha(ROOT / "configs/c0.json")}
