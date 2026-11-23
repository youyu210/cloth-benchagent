import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchagent.common import ROOT, IMPLEMENTATION, source_hashes, sha, read, write, now
expected = read(ROOT / "versions/source_manifest.json")["files"]
if source_hashes() != expected:
    raise SystemExit("Source changed since reviewed adaptation; do not seal")
files = {p.name: sha(p) for p in sorted((ROOT / "build/bin").iterdir())
         if p.suffix.lower() in (".dll", ".exe")}
if "gipc.exe" not in files:
    raise SystemExit("Missing executable")
write(ROOT / "versions/runtime_manifest.json", {
    "implementation_commit": IMPLEMENTATION,
    "adaptation": "benchagent-observability-v1", "built_at": now(),
    "source_manifest_sha256": sha(ROOT / "versions/source_manifest.json"),
    "files": files, "build_log": "build/build.log",
    "dependency_manifest_sha256": sha(ROOT / "versions/dependency_manifest.json"),
    "toolchain_note": "CUDA 12.6, MSVC 14.44, explicit allow-unsupported-compiler; runtime untested"})
