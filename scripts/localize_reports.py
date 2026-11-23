"""Render Chinese views from archived JSON; never launch or revalidate a run."""

import copy
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchagent.common import ROOT, now, read, sha, write
from benchagent.reporter import render_run_markdown, render_suite_markdown

def localize():
    runs = ROOT / "runs"
    # Preserve original report Markdown as well as machine evidence.
    evidence = [p for p in runs.rglob("*")
                if p.is_file() and p.suffix in (".json", ".jsonl", ".csv", ".log", ".md")
                and not p.name.endswith(".zh-CN.md")]
    before = {p.relative_to(ROOT).as_posix(): sha(p) for p in evidence}
    outputs = []
    for path in sorted(runs.rglob("report.json")):
        target = path.with_name("report.zh-CN.md")
        target.write_text(
            "> 本报告由既有 JSON 离线转为中文；沿用历史记录的状态与数值，未启动新实验。\n\n"
            + render_run_markdown(read(path)), encoding="utf-8")
        outputs.append(target)
    for path in sorted(runs.rglob("benchmark_summary.json")):
        summary = copy.deepcopy(read(path))
        for task in summary.get("tasks", []):
            link = task.get("report_path", "")
            if link.endswith(".md"):
                translated = link[:-3] + ".zh-CN.md"
                if (path.parent / translated).is_file():
                    task["report_path"] = translated
        target = path.with_name("benchmark_summary.zh-CN.md")
        target.write_text(
            "> 本汇总由既有 JSON 离线转为中文；沿用历史记录的状态与数值，未启动新实验。\n\n"
            + render_suite_markdown(summary), encoding="utf-8")
        outputs.append(target)
    after = {p.relative_to(ROOT).as_posix(): sha(p) for p in evidence}
    if before != after:
        raise RuntimeError("Archived evidence changed during localization")
    index = "# 中文历史报告索引\n\n"
    index += "以下文件仅由既有 JSON 离线转换；保留历史状态与数值，未重新运行或验算仿真。\n\n"
    index += "| 报告类型 | 中文报告 |\n|---|---|\n"
    for target in outputs:
        kind = "套件汇总" if target.name.startswith("benchmark_summary") else "单项报告"
        link = "../" + target.relative_to(ROOT).as_posix()
        index += f"| {kind} | [{target.parent.name}]({link}) |\n"
    (ROOT / "docs/中文历史报告索引.md").write_text(index, encoding="utf-8")
    result = {
        "updated_at_utc": now(), "method": "offline JSON to Chinese Markdown",
        "new_simulation_runs": 0,
        "original_evidence_unchanged": before == after,
        "outputs": [p.relative_to(ROOT).as_posix() for p in outputs],
        "original_evidence_sha256": before,
    }
    write(ROOT / "docs/中文历史报告转换.json", result)
    return result

if __name__ == "__main__":
    result = localize()
    print(f"已离线生成 {len(result['outputs'])} 份中文报告；原始报告和结构化证据哈希一致。")
