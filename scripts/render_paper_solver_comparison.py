"""Render the paper table from a successfully validated comparison bundle."""
import csv
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchagent.common import ROOT, read, sha


def main(bundle):
    bundle = bundle.resolve()
    summary = read(bundle / "comparison.json")
    runs = read(bundle / "runs.json")
    if summary.get("status") != "PASS" or len(runs) != 6:
        raise ValueError("Only complete, validated runs may be published")
    identity = read(bundle / "build_and_inputs.json")
    conditions = runs[0]["metadata"]
    names = {"original": "原版", "modified": "改进版"}
    header = ["轮次", "版本", "帧数", "物理步总耗时（秒）", "平均帧耗时（毫秒）",
              "Newton 总次数", "PCG 总次数", "碰撞对累计", "进程墙钟（秒）"]
    rows = []
    for run in runs:
        rows.append([run["directory"].split("-")[1], names[run["version"]], run["frames"],
                     f'{run["step_total_s"]:.6f}', f'{run["step_mean_ms"]:.6f}',
                     run["newton_total"], run["pcg_total"], run["collision_counter_total"],
                     f'{run["wall_s"]:.6f}'])
    with (bundle / "对照结果.csv").open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(header)
        writer.writerows(rows)

    gpu = runs[0]["gpu_before"][0].split(",")
    improvement = summary["speedup"]
    lines = [
        "# 表 3 · 原版与改进版实测对照", "",
        f"场景为软体／刚体／布料耦合（源码编号 {conditions['scene_no']}）。"
        f"两版均使用 {gpu[2].strip()}，驱动 {gpu[3].strip()}，CUDA 可见设备 0。"
        f"时间步为 {conditions['dt']} 秒，每次 {conditions['frames']} 帧。"
        f"两版均进行 3 次独立进程测试，顺序依次为原版／改进版、改进版／原版、原版／改进版。", "",
        f"输入核对涵盖网格、分区和参数文件。两版各次运行的初始顶点、速度、质量、边界类型和四面体拓扑字节一致。"
        f"每次均核对 1–{conditions['frames']} 连续帧、完成标记、有限坐标及迭代预算。"
        f"PCG 容差为 {conditions['pcg_tolerance']}，Newton 容差为 {conditions['newton_tolerance']}。", "",
        "原版以 c49982f 为基线，增加定帧、同步墙钟和数据输出观测。改进版为同基线派生的 3a45087f 共享实现及封存观测适配，开启双容差残差补偿 TOI、PCG 双段 CUDA Graph 和 indexed 归约。", "",
        "| " + " | ".join(header + ["逐次证据"]) + " |",
        "| " + " | ".join(["---"] * (len(header) + 1)) + " |",
    ]
    for row, run in zip(rows, runs):
        d = run["directory"]
        refs = f"[逐帧]({d}/raw/frames.csv) · [日志]({d}/stdout.log) · [命令]({d}/command.json)"
        lines.append("| " + " | ".join(map(str, row + [refs])) + " |")
    lines += ["", "| 版本 | 平均帧耗时（毫秒） | 样本标准差（毫秒） | 平均 Newton 总数 | 平均 PCG 总数 |",
              "|---|---:|---:|---:|---:|"]
    for version, name in names.items():
        group = [r for r in runs if r["version"] == version]
        stats = summary["statistics"][version]
        lines.append(f"| {name} | {stats['mean_ms']:.3f} | {stats['sd_ms']:.3f} | "
                     f"{statistics.mean(r['newton_total'] for r in group):.2f} | "
                     f"{statistics.mean(r['pcg_total'] for r in group):.2f} |")
    change = (1 - 1/improvement) * 100
    lines += [
        "",
        f"按三次运行的每帧均值，原版与改进版耗时之比为 **{improvement:.3f}×**，改进版每帧耗时变化为 **{-change:+.2f}%**。"
        "该比值反映本场景和硬件上的整套实现差异。实验未拆分各优化贡献，物理精度和末态等价性需要另行验证。", "",
        "求解计时从 IPC_Solver 入口到 CUDA 同步完成并取得内部计时结果，包含首帧准备，排除初始化、原生画面及报告落盘。"
        "进程墙钟含初始化和证据采集。Newton 使用每帧实际迭代计数，PCG 使用求解器返回的迭代数之和，碰撞采用求解器累计对数。", "",
        f"- 原版观测构建 SHA256 为 {identity['original_binary_sha256']}。",
        f"- 改进版构建 SHA256 为 {identity['modified_binary_sha256']}。",
        "- [构建和输入证据](build_and_inputs.json) · [逐次汇总](runs.json) · [统计汇总](comparison.json) · [中文 CSV](对照结果.csv)。",
        "- [原版适配补丁](../../../versions/paper-original-observation.patch) · [上游文件核验](../../../versions/paper-original-upstream-verification.json) · [原版构建日志](../../../build/paper-original-final.log) · [对照脚本](../../../scripts/run_paper_solver_comparison.py)。",
        "",
    ]
    (bundle / "README.md").write_text("\n".join(lines), encoding="utf-8")
    files = {p.relative_to(bundle).as_posix(): sha(p) for p in sorted(bundle.rglob("*"))
             if p.is_file() and p.name != "evidence_sha256.json"}
    import json
    (bundle / "evidence_sha256.json").write_text(
        json.dumps(files, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Paper table and {len(files)} evidence hashes saved in {bundle}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
