"""Verify an allowed and a rejected comparison using real archives."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchagent.app_service import AppService
from benchagent.common import ROOT, write
from benchagent.model_gateway.disabled import DisabledGateway

LEFT = "job-fe6c976fb96a42e7b727008c28c2fa66"
RIGHT = "job-9ffc87baa70b4984a992a2381c1c9d79"
CASE = "frames-29"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--diagnostic-job", required=True)
    parser.add_argument("--diagnostic-objective", required=True)
    options = parser.parse_args()
    app = AppService()
    before = {p.name for p in (ROOT / "runs").iterdir() if p.is_dir()}
    positive = app.comparisons.create((LEFT, CASE), (RIGHT, CASE))
    negative = app.comparisons.create((LEFT, CASE), (options.diagnostic_job, CASE))
    script = app.analysis_scripts.generate(options.diagnostic_objective, DisabledGateway())
    assert script["execution_status"] == "BLOCKED_NO_OS_ISOLATION"
    assert script["generation_status"] == "BLOCKED_MODEL_NOT_CONFIGURED"
    assert before == {p.name for p in (ROOT / "runs").iterdir() if p.is_dir()}
    for item in (positive, negative):
        app.objectives._check_sources(item["plan"]["input_sha256"])
    base = ROOT / "docs/BenchAgent_论文材料/目标驱动案例" / positive["comparison_id"]
    write(base / "comparisons.json", {"comparable": positive, "diagnostic_rejected": negative})
    write(base / "script_blocked.json", script)
    print(json.dumps({"evidence": str(base), "allowed": positive["status"], "rejected": negative["status"],
                      "metrics": positive["metrics"], "positive_reasons": positive["reasons"],
                      "negative_reasons": negative["reasons"]}, ensure_ascii=False))
    assert positive["status"] == "COMPARABLE" and positive["metrics"]["sample_count_per_side"] == 29
    assert negative["status"] == "NOT_COMPARABLE" and negative["metrics"] is None
    assert any("诊断插桩" in item for item in negative["reasons"])


if __name__ == "__main__":
    main()
