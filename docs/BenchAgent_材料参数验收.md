# BenchAgent 材料参数改造与验收

2026-11-13。独立材料构建编译成功，验证使用 CUDA 12.6、Visual Studio 2022 Build Tools、Ninja 和 NVIDIA GeForce RTX 3090 Ti。原生可执行文件 SHA-256 为 8f29f2ad08ce348d1c3785c8f9780a416771dfaa88023b182cb739d009945c0a。原始源码和原有封存构建的清单均通过复核。

[材料参数能力清单](BenchAgent_材料参数能力清单.md) · [独立构建清单](../versions/native-material-manifest.json) · [CUDA 编译日志](../build/native-material-final.log)

## 使用方式

1. 在「智能测试」选择「智能测试（DeepSeek V4）」或「智能测试（规则对话）」。使用 DeepSeek 时先在「系统设置 → 模型接口」保存密钥。
2. 输入「把固定布料的杨氏模量改成原来的两倍，运行20帧」。预览会展示默认值、修改前值、计划值、单位和预期影响。数字未带单位时按表格的标准单位解释。
3. 可以继续输入「把密度改为300 kg/m³，其他参数保持不变」，再输入「把泊松比改成0.3」。相对变化使用同场景上一份有效材料计划。切换场景时使用新场景默认值。输入「恢复这个场景的默认材料参数」可预览一次默认配置任务。
4. 也可以在「表单配置」选择支持的场景，打开「布片材料参数」，勾选「为本次任务配置材料」。用「恢复场景默认值」清除本次覆盖。需要抓帧时勾选「采集原生画面」。
5. 核对冻结计划后点击「确认并开始执行」。结束后在「测试报告 → 运行配置」查看实际材料，在「概览 → 原生 OpenGL 画面」查看关键帧。在「目标驱动实验分析 → 对照两组真实归档」选择「单项布片材料参数」可对照两份合格归档。

定性要求先给出面内刚度、弯曲与接触的解释，并请求明确的幅度或数值。规划与校验阶段只产生候选方案和冻结任务。

## 实现与证据

| 层级 | 代码与行为 |
| --- | --- |
| 场景与参数 | [material_catalog.json](../configs/material_catalog.json) 给出默认值、单位、范围与适用对象。[materials.py](../benchagent/materials.py) 校验并转换为原生配置。 |
| 规划与确认 | [material_dialogue.py](../benchagent/material_dialogue.py) 解析数值、单位、倍数、百分比和同场景上下文。[DeepSeek 网关](../benchagent/model_gateway/deepseek.py) 使用受限 JSON 计划并校验响应与明确指令一致。[plan_service.py](../benchagent/plan_service.py) 冻结请求和材料构建身份。 |
| 原生加载 | [runner.py](../benchagent/runner.py) 在独立用例写 inputs/material.json。CIPC_MATERIAL_CONFIG 由执行器指向此文件。[benchagent_material.inc](../versions/native-material-source/StiffGIPC/benchagent_material.inc) 在场景初始化后、initFEM 前应用配置。 |
| GPU 生效 | 原生 FEM 初始化计算拉伸、剪切、弯曲系数和顶点质量，原有 CUDA 求解路径使用这些系数与设备数组。GPU 质量在初始化后回读。执行器核对 material_effective.json、系数重算和 masses.f64，材料检查记为 M01。 |
| 可视化与导出 | [ui_app.py](../benchagent/ui_app.py) 和 [reporter.py](../benchagent/reporter.py) 展示默认、计划及实际材料。原始 Markdown、JSON 和 ZIP 证据保存材料明细。原有 PDF 导出继续使用已验收的渲染器。[archive_comparison.py](../benchagent/archive_comparison.py) 核对构建、场景、时间步、初始位置与速度、边界、网格及其他设置。只有体密度单项变化时，允许相应的初始质量差异。 |

具名场景的 scene_setup.json 仍记录原生场景预设与角色信息。任务实际材料以 material_effective.json 的 effective 字段为准。每个材料用例保留命令、源码身份、配置与生效记录、逐帧计时和迭代，以及开启抓帧时的原生图像。

## T1–T8 结果

| 验收 | 状态 | 结果 |
| --- | --- | --- |
| T1 默认基线 | 通过 | 固定布料 20/20 帧，50 000 Pa、200 kg/m³、泊松比 0.49。 [任务报告](../runs/job-5b66f42be78d4e38972bebd045c76583/cases/smoke-20/report.md) |
| T2 单项修改 | 通过 | 固定布料 20/20 帧，50 000 → 100 000 Pa。拉伸系数约从 16 778.52 增至 33 557.05，GPU 顶点质量文件哈希一致。与 T1 的对应末帧顶点最大距离差为 0.020982 m。 [任务报告](../runs/job-3dacaa56bd3d4f84aa9aad30447291cf/cases/smoke-20/report.md) · [原生生效记录](../runs/job-3dacaa56bd3d4f84aa9aad30447291cf/cases/smoke-20/raw/material_effective.json) |
| T3 连续修改 | 通过 | 固定布料 20/20 帧，保留 100 000 Pa，体密度 200 → 300 kg/m³。回读布片总质量 0.288 → 0.432 kg，泊松比保持 0.49。与 T2 的末帧最大距离差为 0.010118 m。 [任务报告](../runs/job-fc93bade84214750874ab635ca7a8db7/cases/smoke-20/report.md) |
| T4 DeepSeek 到 GPU | 通过 | 真实 API 调用一次并通过计划校验。悬挂布料泊松比 0.243 → 0.3，确认后完成 20/20 帧。 [模型计划与生效证据](material_gpu_evidence/deepseek-65414e6f18a8458994212ba4314f90fb/result.json) · [任务报告](../runs/job-8c191e9efbd448e19876ccba93ae9ef9/cases/smoke-20/report.md) |
| T5 多轮继承 | 通过 | 继续在 T3 草稿中修改泊松比为 0.3。冻结计划保留 100 000 Pa、300 kg/m³ 和 20 帧。 [冻结请求](material_gpu_evidence/acceptance-374fcca3717f4c2b9d72661bfee704e3/result.json) |
| T6 反例 | 通过 | 自动化覆盖越界、未知参数、非有限数值、错单位、场景冲突、对象错误和模型额外执行字段。 [材料专项测试](../tests/test_materials.py) |
| T7 恢复默认 | 通过 | 固定布料 20/20 帧。原生默认值、实际值、派生系数及 GPU 质量文件与 T1 一致。两次独立运行末帧最大距离为 6.235×10⁻⁸ m。 [任务报告](../runs/job-d2f1a2e2d6e34c419fb221b26624d31d/cases/smoke-20/report.md) |
| T8 跨场景隔离 | 通过 | 球体与布料 20/20 帧，布片密度为 300 kg/m³，膜杨氏模量采用该场景默认的 1 000 000 Pa。历史球布归档和原源码清单均通过复核。 [任务报告](../runs/job-0a48191b3ca645118579e1fcd3c6ba03/cases/smoke-20/report.md) |

所有 20 帧任务归档了原生第 0、10、20 帧。T1/T2 以及 T2/T3 通过单变量可比性检查。T1/T3 同时改变两个材料参数，系统拒绝将其标记为单变量对照。位移和耗时是相同初态、求解设置和构建下的单次观测。

## 复现

在 D:\benchagent 运行以下命令。第一条验证程序会创建五个实际 GPU 任务。第二条读取当前进程的 DeepSeek 密钥，或通过隐藏输入接收密钥，校验后提交一项实际 GPU 任务。两条命令都会创建独立验收目录和运行归档。

~~~powershell
& '.\.venv\Scripts\python.exe' scripts/verify_material_gpu.py --run
& '.\.venv\Scripts\python.exe' scripts/verify_material_deepseek.py
& '.\.venv\Scripts\python.exe' -m unittest discover -s tests
~~~

需要重新编译材料程序时，使用 [独立构建脚本](../scripts/build_native_material.ps1)。原有默认场景程序和材料构建分别封存。恢复默认只需提交空材料覆盖的新任务。

[本轮 GPU 归档与对照索引](material_gpu_evidence/acceptance-374fcca3717f4c2b9d72661bfee704e3/result.json)
