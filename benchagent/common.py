import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRAME_TARGETS = range(1, 1001)
BASE = "c49982fac7767225c7775683734c89eb74081142"
IMPLEMENTATION = "3a45087fdbd3b0f1ca6e12ef2de3ab91b0db42df"

def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def read(path):
    def invalid(value):
        raise ValueError("Nonfinite JSON constant: " + value)
    return json.loads(Path(path).read_text(encoding="utf-8-sig"),
                      parse_constant=invalid)

def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    # Windows readers/antivirus may briefly deny delete-sharing. Retry only
    # publishing this same fsynced temporary file, never the operation itself.
    for attempt in range(50):
        try:
            os.replace(tmp, path)
            break
        except PermissionError as exc:
            if getattr(exc, "winerror", None) not in (5, 32, 33) or attempt == 49:
                raise
            time.sleep(min(.01 * (attempt + 1), .05))

def now():
    return datetime.now(timezone.utc).isoformat()

def source_hashes():
    base = ROOT / "simulator"
    return {p.relative_to(base).as_posix(): sha(p)
            for p in sorted(base.rglob("*")) if p.is_file()}
