# 布料仿真 BenchAgent 配置、构建与运行说明

文档日期 2026-11-16。项目目录为 D:\benchagent，适用于 Windows x64。工作台操作、可配置参数、任务确认和结果解读见 [功能说明](BenchAgent_当前版本使用说明书.md)。

## 1. 启动已配置的工作台

在任意目录打开 PowerShell，运行以下命令。

~~~powershell
& 'D:\benchagent\scripts\start_workbench.cmd'
~~~

8514 空闲时，脚本会启动服务，保持启动终端打开即可。已有 BenchAgent 且通过健康检查时，脚本显示 `BenchAgent is already running. Open http://127.0.0.1:8514/` 并返回成功。进入工作台后，在「系统设置」点击「检查运行环境」，核对 GPU 和构建就绪状态。 [本地工作台](http://127.0.0.1:8514/)

## 2. 安装依赖和配套文件

| 组件 | 要求 |
| --- | --- |
| 工作台 | 64 位 Python 3.12 和项目虚拟环境 `.venv`。当前项目核对为 Python 3.12.14、Streamlit 1.66.1。 |
| 原生运行 | 可用的 NVIDIA 驱动和 GPU，`nvidia-smi` 能查询设备。已验收 NVIDIA GeForce RTX 3090 Ti。常规执行由固定配置绑定 CUDA 设备 0。 |
| 原生构建 | CUDA Toolkit 12.6、Visual Studio 2022 C++ Build Tools、Windows SDK、CMake、Ninja 及 `dependencies` 中的 Eigen3、nlohmann_json、GLEW 和 FreeGLUT。 |
| DeepSeek | 表单和规则对话可离线使用。DeepSeek V4 Pro 需要可用网络和 API 密钥。 |

首次部署或迁移后，若项目虚拟环境缺失或原虚拟环境不可用，使用本机 Python 3.12 建立环境。已存在可用的环境可直接进入下一步。先确认 `python --version` 指向 3.12。如果 Python 未加入 PATH，把命令中的 `python` 换为已安装解释器的绝对路径。

~~~powershell
Set-Location -LiteralPath 'D:\benchagent'
python --version
python -m venv .venv
& '.\.venv\Scripts\python.exe' -m pip install -r '.\requirements.lock.txt'
& '.\.venv\Scripts\python.exe' -m pip check
nvidia-smi
~~~

迁移现有工作目录时，保留源码、资产、构建、运行 DLL 及其对应清单。项目脚本和已生成的 CMake 缓存包含工具或源码绝对路径。优先保留 `D:\benchagent` 目录结构，换机时根据本机的 CUDA 和 Visual Studio 安装路径核对脚本中的工具路径。 [Python 锁定依赖](../requirements.lock.txt)

## 3. 原生构建和校验

已有配套程序时可以直接运行。需要重建时，先等当前任务到达终态，再停止正在使用该构建的工作台。

| 执行场景 | 原生程序 | 构建与身份清单 |
| --- | --- | --- |
| 常规仿真 | `build/bin/gipc.exe` | `scripts/build.ps1` 从 `simulator` 构建，发布程序与运行依赖并生成身份清单。 [运行清单](../versions/runtime_manifest.json) · [源码清单](../versions/source_manifest.json) |
| 普通原生抓帧 | `build/native-frames/gipc.exe` | 优先复用配套程序和 `versions/native-frames-source`。 [抓帧清单](../versions/native_frames_manifest.json) · [构建日志](../build/native-frames-final.log) |
| 箱体排列 | `build/native-box-scene/gipc.exe` | 优先复用配套程序和源码。维护用编译脚本生成程序，重编后的发布需要同步核验并登记身份。 [箱体构建脚本](../scripts/build_native_box_scene.ps1) · [箱体清单](../versions/native-box-scene-manifest.json) |
| 布片材料任务 | `build/native-material/gipc.exe` | 独立脚本从 `versions/native-material-source` 构建，并调用封存脚本生成材料构建清单。支持在一次任务中同时加载材料配置与采集原生画面。 [材料构建脚本](../scripts/build_native_material.ps1) · [材料清单](../versions/native-material-manifest.json) |

在已允许执行本地项目脚本的 PowerShell 中，从项目根目录按需要运行对应脚本。

~~~powershell
Set-Location -LiteralPath 'D:\benchagent'
& '.\scripts\build.ps1'
& '.\scripts\build_native_material.ps1'
~~~

默认构建日志在 `build/build.log`，材料构建日志在 `build/native-material-final.log`。材料脚本使用项目内 Python 生成清单，校验独立材料源码与抓帧源码的继承关系，并记录材料目录、源码和 EXE 的 SHA-256。计划预览与执行均核对构建身份。运行时通过项目 `build/bin` 查找 CUDA 和图形组件 DLL。 [材料封存脚本](../scripts/seal_native_material.py)

核对命令行环境。第一条检查默认求解程序、源码、配置和 GPU，将结果保存至 `docs/preflight_latest.json`。第二条逐项检查抓帧、箱体与材料构建的清单，输出对应的程序路径和哈希。

~~~powershell
Set-Location -LiteralPath 'D:\benchagent'
& '.\.venv\Scripts\python.exe' main.py preflight
& '.\.venv\Scripts\python.exe' -c "from benchagent.native_render import runtime_identity; print(runtime_identity()); print(runtime_identity(variant='native-box-scene')); print(runtime_identity(variant='native-material'))"
~~~

## 4. DeepSeek 与任务配置

DeepSeek 默认模型为 `deepseek-v4-pro`，工作台使用 `https://api.deepseek.com`。在「系统设置 → 模型接口」填写密钥并点击「保存密钥」，再进入「智能测试（DeepSeek V4）」发送需求。界面保存的密钥属于当前会话。工作台服务重启或浏览器建立新会话后，重新保存会话密钥即可。也可在启动工作台前通过进程环境提供 `DEEPSEEK_API_KEY`，页面会话密钥优先。`.env.example` 是配置示例，系统读取实际环境变量。 [DeepSeek 网关选择](../benchagent/model_gateway/__init__.py)

场景目录定义场景标识、帧数范围、资产和抓帧能力。材料目录定义可编辑对象、默认值、单位和范围。固定求解配置记录默认 GPU 绑定及算法参数。正常提交时由工作台冻结计划、选定程序并生成用例内的 `inputs/material.json`。原生程序通过执行器设置的 `CIPC_MATERIAL_CONFIG` 读取该文件，实际值写入 `raw/material_effective.json`。 [场景目录](../configs/scene_catalog.json) · [材料目录](../configs/material_catalog.json) · [固定求解配置](../configs/c0.json)

## 5. 运行与复现

**工作台快速验收。** 在「智能测试 → 表单配置」选择「固定布料」，固定帧数选 20，勾选「采集原生画面」。点击「预览计划」，确认场景与帧数后提交。核对已完成 20/20 帧并查看第 0、10、20 帧。验证材料链路时，另建任务并启用「为本次任务配置材料」，将膜杨氏模量改为 100 000 Pa。结束后在「测试报告 → 运行配置」检查实际值，再查看材料校验 M01。 [已有材料运行报告](../runs/job-3dacaa56bd3d4f84aa9aad30447291cf/cases/smoke-20/report.md)

**命令行运行默认场景。** 以下命令会提交一次真实 GPU 仿真，使用默认求解程序。命令行兼容入口的 `run --frames` 支持 20、50、100 帧。材料任务、自由帧数和画面选项可在工作台配置。

~~~powershell
Set-Location -LiteralPath 'D:\benchagent'
& '.\.venv\Scripts\python.exe' main.py run --frames 20 --timeout 600
~~~

**复现材料与目标分析验收。** 在项目根目录按需要执行。自动化测试检查 Python、计划校验和界面流程。目标反馈复验读取归档并另存分析证据。两条材料验收命令会实际提交 GPU 任务，分别执行五项和一项 20 帧仿真。DeepSeek 脚本读取进程密钥或通过隐藏输入接收密钥，并实际调用一次 API。运行材料验收脚本前先等工作台任务结束并停止工作台，以释放独立工作器与 GPU 执行锁。 [材料验收与预期结果](BenchAgent_材料参数验收.md) · [目标分析验收](BenchAgent_目标驱动验收报告.md)

~~~powershell
Set-Location -LiteralPath 'D:\benchagent'
& '.\.venv\Scripts\python.exe' -m unittest discover -s tests
& '.\.venv\Scripts\python.exe' scripts/verify_objective_feedback.py
& '.\.venv\Scripts\python.exe' scripts/verify_material_gpu.py --run
& '.\.venv\Scripts\python.exe' scripts/verify_material_deepseek.py
~~~

| 输出 | 路径与用途 |
| --- | --- |
| 工作台计划和任务状态 | `data/planning`，保存确认、运行记录、分析和对照。 |
| 仿真归档 | `runs/<任务标识>/cases/<用例标识>` 下保存 `plan.json`、`run_meta.json`、日志、原始指标和任务报告。旧单次命令行任务直接保存在 `runs/<运行标识>`。 |
| 原始材料和画面 | 用例内 `inputs/material.json`、`raw/material_effective.json`、`raw/masses.f64`，以及启用时的 `raw/native_frames`。 |
| 报告与验收 | 工作台导出进入 `exports`。材料复现索引进入 `docs/material_gpu_evidence`。 |

## 6. 重启和常见问题

关闭浏览器后，已提交任务由工作台服务继续执行。更新 Python 代码或切换构建后，待任务结束，回原启动终端按 `Ctrl+C` 停止服务，再执行第 1 节命令。重启后刷新页面。

| 现象 | 处理 |
| --- | --- |
| `BenchAgent is already running` | 直接打开或刷新本地工作台。需要加载新版本时按本节步骤重启。 |
| `Port 8514 is already in use. Listening PID(s): …` | 用下方命令核对进程。待端口释放后重试，或者为工作台选择空闲端口。 |
| `BenchAgent is listening on 8514 but did not pass its health check.` | 查看原启动终端。服务仍在初始化时可稍后重试。若终端有报错，按实际错误处理后重启。 |
| `Port 8514 is not available` | 旧脚本或直接运行 Streamlit 时可能显示此提示。用第 1 节的 CMD 入口重新检查。 |
| PowerShell 禁止执行 `.ps1` | 日常启动使用 `start_workbench.cmd`。需要编译时使用允许运行项目脚本的 PowerShell。 |
| 执行占用或仍有旧工作器 | 在「系统设置 → 执行占用」查看状态。异常中止后，先核对所有者进程和 GPU 子进程，再由维护者处理遗留记录。 |
| 预览提示构建身份不匹配 | 核对对应 EXE、源码、资产及清单来自同一构建。材料程序可用第 3 节的脚本重新构建与封存。 |
| DeepSeek 调用失败 | 检查当前会话密钥和网络。可用表单或规则对话独立检查本地任务链路。 |
| 找不到原生画面或材料实际值 | 在报告中选中正确的用例，查看本次是否启用画面采集或材料任务，再核对对应检查及日志。 |

查询 8514 的监听进程。

~~~powershell
$benchListeners = Get-NetTCPConnection -LocalPort 8514 -State Listen -ErrorAction SilentlyContinue
$benchListeners | Select-Object LocalAddress, LocalPort, OwningProcess
$benchListeners | ForEach-Object { Get-Process -Id $_.OwningProcess } | Select-Object Id, ProcessName
~~~

需要使用其他端口时，先确认同一项目的原工作台已停止。以下命令以空闲的 8520 为例，启动后访问对应地址。 [备用端口工作台](http://127.0.0.1:8520/)

~~~powershell
Set-Location -LiteralPath 'D:\benchagent'
& '.\.venv\Scripts\python.exe' -m streamlit run '.\ui.py' --server.address=127.0.0.1 --server.port=8520 --server.headless=true --server.fileWatcherType=none --browser.gatherUsageStats=false
~~~
