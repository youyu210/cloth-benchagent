"""Compare native executable output with the archived workbench frames."""
import os
from pathlib import Path
import subprocess
import sys
from time import monotonic, sleep
from uuid import uuid4

from PIL import Image, ImageChops
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchagent.common import ROOT, read, sha, write
from benchagent.conversation_service import SCENE_NUMBERS, scene_args
from benchagent.native_render import EXECUTABLE, frame_name
from benchagent.runner import exclusive

QA = ROOT / "docs/qa/benchagent_v4_upgrade"


def main():
    deadline = monotonic() + 150
    while (ROOT / "data/planning/.manager.lock").exists() or (ROOT / "runs/.execution.lock").exists():
        if monotonic() >= deadline:
            raise TimeoutError("Worker did not finish")
        sleep(.5)
    evidence = read(QA / "real_runs.json")
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("GIPC_", "CIPC_"))}
    env.update(CUDA_VISIBLE_DEVICES="0", CUDA_DEVICE_ORDER="PCI_BUS_ID")
    env["PATH"] = str(ROOT / "build/bin") + os.pathsep + env["PATH"]
    output = QA / ("direct-" + uuid4().hex[:8])
    output.mkdir()
    comparisons = {}
    with exclusive():
        for label in ("T1", "T2"):
            item = evidence[label]
            scene, count = item["scene"], item["frames"]
            folder = output / label
            folder.mkdir()
            archived = ROOT / "runs" / item["job_id"] / "cases" / item["case_id"] / "raw"
            args = scene_args(read(ROOT / "configs/c0.json")["args"], scene)
            cmd = [str(ROOT / EXECUTABLE), *args, "--native-render-frames",
                   "--benchmark-frames", str(count), "--benchmark-output-dir", str(folder / "raw")]
            with (folder / "stdout.log").open("wb") as stdout, (folder / "stderr.log").open("wb") as stderr:
                subprocess.run(cmd, env=env, cwd=ROOT / "simulator", timeout=90,
                               stdout=stdout, stderr=stderr, check=True)
            meta = read(folder / "raw/native_render.json")
            assert meta["scene_no"] == SCENE_NUMBERS[scene]
            assert meta["frames"] == [0, count // 2, count]
            equal = {}
            for frame in meta["frames"]:
                name = frame_name(frame)
                with Image.open(folder / "raw" / name) as direct, Image.open(archived / name) as workbench:
                    equal[str(frame)] = ImageChops.difference(direct, workbench).getbbox() is None
                    if frame == count:
                        direct.save(QA / f"{label}-direct-final.png")
            # Compare separately with the previously validated original headless workbench executable.
            reference = {
                "T1": "job-ee137fd141be4cc497a939bdf05a8b47",
                "T2": "job-ad6ae74d55d04e669c23bf88bff3c6b1",
            }[label]
            headless = ROOT / "runs" / reference / "cases/smoke-20/raw"
            initial_equal = sha(headless / "initial.f64") == sha(archived / "initial.f64")
            a = np.fromfile(headless / "final.f64", dtype="<f8")
            b = np.fromfile(archived / "final.f64", dtype="<f8")
            comparisons[label] = {
                "command": cmd, "direct_archive": folder.relative_to(ROOT).as_posix(),
                "workbench_job": item["job_id"], "equal_pixels": equal,
                "headless_job": reference, "headless_initial_equal": initial_equal,
                "headless_final_max_abs_delta": float(np.max(np.abs(a-b))),
            }
    write(QA / "direct_comparison.json", comparisons)
    print(comparisons)


if __name__ == "__main__":
    main()
