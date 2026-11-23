"""Verify one DeepSeek material plan through the confirmed GPU execution path."""
from getpass import getpass
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from benchagent.app_service import AppService
from benchagent.common import read, write
from benchagent.materials import MATERIAL_SCENES
from benchagent.planning_store import PlanningStore, new_id
from verify_material_gpu import EVIDENCE, run_case


def verify(api_key):
    destination = EVIDENCE / new_id("deepseek")
    destination.mkdir(parents=True)
    app = AppService(ROOT, store=PlanningStore(destination / "planning"))
    try:
        assert not (ROOT / "runs/.execution.lock").exists()
        assert not (ROOT / "data/planning/.manager.lock").exists()
        result = app.preview(None, "把悬挂布料的泊松比改为0.3，运行20帧并采集原生画面。", 0,
                             use_model=True, api_key=api_key)
        plan = result["plan"]
        audit = plan["provenance"]["planning_audit"]
        assert audit["provider_calls"] == 1 and "provider:MATERIAL_PLAN_VALIDATED" in audit["stages"]
        assert not app.jobs()
        expected = {**MATERIAL_SCENES["cloth-hang"]["defaults"], "poisson_ratio": .3}
        folder = run_case(app, plan, expected)
        report = read(folder / "report.json")
        write(destination / "result.json", {"status": "PASS", "case": folder.relative_to(ROOT).as_posix(),
                                           "plan": plan, "audit": audit,
                                           "material": report["material_evidence"]})
        print(destination / "result.json")
    finally:
        app.close()


if __name__ == "__main__":
    key = os.environ.get("DEEPSEEK_API_KEY") or getpass("DeepSeek API key (hidden): ")
    if not key.strip():
        raise SystemExit("DeepSeek key unavailable")
    try:
        verify(key)
    finally:
        del key

