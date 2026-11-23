"""Seal the independent material executable and verify its source lineage."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from benchagent.common import read, sha, write

base = "versions/native-frames-source/"
source = "versions/native-material-source/"
adapter = source + "StiffGIPC/benchagent_material.inc"
main = source + "StiffGIPC/gl_main.cu"
executable = "build/native-material/gipc.exe"

base_manifest = read(ROOT / "versions/native_frames_manifest.json")["files"]
files = {}
for name, digest in base_manifest.items():
    if not name.startswith(base):
        continue
    target = source + name.removeprefix(base)
    actual = sha(ROOT / target)
    if target != main and actual != digest:
        raise SystemExit(f"Unexpected source difference: {target}")
    files[target] = actual
for name in (adapter, executable, "configs/material_catalog.json"):
    files[name] = sha(ROOT / name)
write(ROOT / "versions/native-material-manifest.json", {
    "schema": "native-material-build-v1",
    "base_manifest_sha256": sha(ROOT / "versions/native_frames_manifest.json"),
    "files": files,
})
print(f"Sealed {len(files)} inputs, executable SHA-256 {files[executable]}")

