# BenchAgent 目标驱动实验验收报告

2026-11-12，D:\benchagent。正式验收使用一组已归档的球体与布料仿真和一组新诊断子任务。项目根目录的 git status 返回未检测到 Git 仓库。全量自动化回归运行 241 项并通过，其中新增 12 项。自动化模型替身与真实运行验收分别记录。

| 编号 | 结果 | 证据与判定 |
| --- | --- | --- |
| E1 真实归档直接分析 | PASS | 耗时和迭代目标用原 29 帧归档直接结束。含计数和画面的完整目标再次执行时，自动复用已经存在的诊断归档，29/29 帧，GPU 新任务 0。独立复算排序、中位数和分组约束计数。[最终只读复验](BenchAgent_论文材料/目标驱动案例/objective-154b69b3bda24de2825df2d3c70b52f9/evaluation.json) |
| E2 缺失证据反馈与补测 | PASS | 普通归档缺少逐帧约束计数，生成明确新增信息的待确认计划。按验收预算显式确认一次，子任务完成 29/29 帧。请求 CCD 分阶段计时时得到有限结论，零新增任务。原始父任务 34 个文件的 SHA-256 全部保持一致。[动作、预算、授权和哈希](BenchAgent_论文材料/目标驱动案例/objective-fdab751746b745189f58ac18249e5aea/evaluation.json) |
| E3 真实 API 工具反馈 | PASS | DeepSeek 实际调用 3 次，三个动作均经 Schema 和允许动作列表校验。后续请求分别使用已读的 29 帧耗时与 29 帧约束覆盖观测。直接复用已完成的原生诊断归档，未提交新 GPU 任务。32 个输入文件哈希保持一致。[真实调用与动作证据](BenchAgent_论文材料/目标驱动案例/objective-8a1238ec5a34454a98a2e4496d684183/deepseek_objective.json) |
| E4 真实归档可比性 | PASS | 两份同配置球体与布料归档覆盖同一 0.01–0.29 s 范围。物理步总耗时分别为 2922.270、2934.196 ms，比值 0.99594。含追踪插桩的归档被拒绝，未产生比值。每侧为一次历史运行。[真实配置检查与拒绝原因](BenchAgent_论文材料/目标驱动案例/comparison-2ee5f7ad2f4a4fa581c3e50ef1518ba7/comparisons.json) |
| E5 临时分析脚本 | BLOCKED | 生成接口、静态审核和预算拦截已实现。本轮真实模型联调只覆盖 E3，未请求脚本在线生成。自动执行因未验证操作系统级隔离而禁用，未生成或执行模型脚本。[运行时阻碍记录](BenchAgent_论文材料/目标驱动案例/comparison-2ee5f7ad2f4a4fa581c3e50ef1518ba7/script_blocked.json) |
| E6 旧功能与报告 | PASS | 全量自动化覆盖原表单、规则、遥测与报告。真实子任务重新读取 29/29 遥测、日志、三帧原生画面和 Markdown 原报告，PDF、Markdown、JSON、CSV、ZIP 均成功导出并逐项校验文件哈希。[报告和画面验证](BenchAgent_论文材料/目标驱动案例/objective-fdab751746b745189f58ac18249e5aea/report_and_visualization.json) |

## 真实诊断结果

场景为 cloth-sphere7，CIPC_SCENE=sphere7，原生编号 1，物理时间步 0.01 s。父任务 job-f0c29c3238ab4eddb961138407946393 的 frames-29 子用例已包含原生画面。诊断子任务为 job-58d6d5f1265f4269a363e98be24344f1，复用 NVIDIA GeForce RTX 3090 Ti、CUDA 设备绑定 0、驱动 610.88 和同一封存原生构建。请求与实际帧数均为 29，退出码为 0，运行校验 PASS。原生画面采集另记 254.456 ms。累计消耗一次新 GPU 任务，验收总预算为两次。

| 观测量 | 高耗时 5 帧中位数 | 其余 24 帧中位数 |
| --- | ---: | ---: |
| 物理步耗时（ms） | 201.572 | 50.362 |
| Newton 迭代（次/步） | 9 | 4 |
| PCG 累计迭代（次/步） | 381 | 94.5 |
| 装配前近距离约束列表峰值（项/步） | 1131 | 0 |

五个高耗时帧为 23、27、22、29、26，按耗时降序。阈值 151.281 ms，整体中位数 56.792 ms，P90 为 172.835 ms，最高耗时 430.436 ms。第 23 帧关联已采集的原生第 29 帧，偏移 +6。第 29 帧精确匹配。图中可辨认固定球及其上方的布料网格。

[原始逐帧 CSV](../runs/job-58d6d5f1265f4269a363e98be24344f1/cases/frames-29/raw/frames.csv) · [Newton 原始追踪](../runs/job-58d6d5f1265f4269a363e98be24344f1/cases/frames-29/raw/newton_trace.jsonl) · [原始日志](../runs/job-58d6d5f1265f4269a363e98be24344f1/cases/frames-29/stdout.log) · [构建、环境与实际命令](../runs/job-58d6d5f1265f4269a363e98be24344f1/cases/frames-29/run_meta.json) · [验收报告原件](../runs/job-58d6d5f1265f4269a363e98be24344f1/cases/frames-29/report.md)

该诊断运行中，耗时排名靠前的帧同时出现更高的迭代与约束列表计数。诊断耗时包含追踪开销，数据只支持组间描述。缺少对象对分类、物理接触事件和分阶段计时，无法推断球布接触次数、穿透、CCD／线搜索瓶颈、收敛质量或性能提升。两份历史对照也不足以作统计显著性判断。

## E3 真实模型复验

2026-11-12 使用隐藏输入提供临时凭据。模型依次选择读取物理步、读取接触追踪、关联原生画面。第二次请求已知 29 帧物理步有效，第三次还已知 29 帧约束追踪覆盖。分析输出包含第 0、14、29 帧的原生画面引用，结论为证据充分。可运行项目内的 [verify_objective_deepseek.py](../scripts/verify_objective_deepseek.py) 并在隐藏提示中输入密钥复现。

## 复现

在 D:\benchagent 打开 PowerShell。启动工作台后，进入「智能测试 → 打开目标驱动实验分析」，输入「分析球体与布料的高耗时帧，对照接触相关计数和 Newton/PCG 迭代，展示关键帧」，输入归档保留「按目标场景查询」，点击「查询归档并分析」。预期直接复用现有诊断证据。

~~~powershell
& '.\scripts\start_workbench.cmd'
& '.\.venv\Scripts\python.exe' scripts/verify_objective_feedback.py
& '.\.venv\Scripts\python.exe' -m unittest discover -s tests
~~~

只读复验命令会保存独立的派生证据，不增加仿真运行。验收程序提供显式的 --run-diagnostic 开关，仅在需要重做一次 29 帧补测且有预算时使用。真实对照复验脚本需要已有诊断任务和目标标识，见 [论文案例索引](BenchAgent_论文材料/目标驱动案例/README.md)。

P0 本地证据闭环和真实 DeepSeek 工具反馈通过。P1 归档对照通过。脚本生成与审核入口可用，真实生成和安全执行尚未验收。可用于论文描述可复现的系统实现与案例，当前没有证据支持模型自主运行、任意代码安全执行或求解器加速等主张。