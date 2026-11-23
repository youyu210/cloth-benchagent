# BenchAgent 材料参数能力清单

## 原生入口

LoadSettings 读取 Assets/scene/parameterSetting.txt。initScene 先执行 set_case4 或 set_case8，具名场景可覆盖文件预设。随后 initFEM 计算 FEM 系数与顶点质量，再将质量、三角形及面积上传到设备。杨氏模量通过 stretchStiff 和 shearStiff 进入现有布料求解核。密度为体密度，按三角形面积 × 厚度 × 密度分配顶点质量。泊松比同时参与膜、弯曲和体 FEM 系数，只开放给无体网格的单布片场景。

| 场景 | 对象 | 膜杨氏模量 Pa | 体密度 kg/m³ | 泊松比 |
| --- | --- | ---: | ---: | ---: |
| 固定布料 fixed-cloth | 单布片 cloth_high.obj | 50 000 | 200 | 0.49 |
| 悬挂布料 cloth-hang | 单布片 cipc_square2K.obj | 821 000 | 472.641509 | 0.243 |
| 球体与布料 cloth-sphere7 | 布片 square_cloth_10k.obj | 1 000 000 | 200 | 0.49，仅查看 |

BenchAgent 本次支持的配置边界为杨氏模量 1 000–10 000 000 Pa、体密度 10–2 000 kg/m³、泊松比 0–0.49。前两项为有限正值的工程配置边界。泊松比采用常规非负区间并避开原生公式的 0.5 奇点。范围边界不等同于所有组合的稳定性验证。

| 只读字段 | 初始化位置与原因 |
| --- | --- |
| 弯曲杨氏模量、厚度 | parameterSetting.txt 与 set_case8 预设。弯曲模量独立于膜模量，厚度同时进入质量和弯曲系数。当前任务保留场景预设。 |
| 摩擦与地面摩擦 | frictionRate 和 gd_frictionRate 为求解器级系数，分别用于场景接触和地面接触，不能按单片布料独立配置。固定布料与球布预设为 0.2，悬挂布料预设为 0.4。 |
| 球体、其他刚体和弹性体 | 球体为固定 ABD 障碍物。体 FEM 使用独立体密度和每四面体模量。布料接口仅作用于三角网格对应的全局布料系数。 |
| 网格、固定点和其他场景 | 场景预设定义资产和边界。多片布料共用求解器布料字段，当前无逐对象覆盖。 |

源码位置见 [initScene 与 initFEM](../versions/native-frames-source/StiffGIPC/gl_main.cu)、[具名布料预设](../versions/native-frames-source/StiffGIPC/benchmark_cloth_v1.inc)、[材料默认文件](../versions/native-frames-source/Assets/scene/parameterSetting.txt) 和 [GPU 求解路径](../versions/native-frames-source/StiffGIPC/GIPC.cu)。材料运行时适配构建放在独立目录，完成一次构建后可按任务加载 JSON。
