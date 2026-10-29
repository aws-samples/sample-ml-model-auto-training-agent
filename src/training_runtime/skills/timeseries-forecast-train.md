---
name: timeseries-demo-forecast-train
description: 用 AutoGluon 做逐小时滚动单步时序预测（多实体长表），按实体评估 7 天（168 小时）MAPE。示例场景，可替换为任意小时级传感器指标
triggers:
  - 训练时序预测（示例）模型
  - AutoGluon timeseries rolling forecast
  - AutoGluon 时序预测训练
inputs:
  - name: train_data_path
    description: 训练数据 CSV 路径（10秒间隔原始数据）
    required: true
  - name: test_data_path
    description: 测试数据 CSV 路径（小时级，用于滚动回灌真值）
    required: true
  - name: ground_truth_path
    description: 真值 CSV 路径（仅用于最终评估比对）
    required: true
  - name: eval_script_path
    description: 官方评估脚本路径（compare_timeseries.py）
    required: true
  - name: output_dir
    description: 结果输出目录（必须使用 runtime 在 prompt 中给出的绝对输出目录，不要用 ./output_combined 这类相对路径）
    default: ""
  - name: target_mape
    description: 目标 MAPE 阈值（%）
    default: 20
  - name: hf_endpoint
    description: HuggingFace 镜像地址（中国区必须设置）
    default: ""
output:
  - 每设备预测 CSV（timestamp, prediction）
  - evaluation_report.json（含每设备 MAPE + 总平均 MAPE）
---

# 逐小时滚动时序预测训练（AutoGluon 自主训练）

对多台设备的小时级指标（`target_value`）做时序预测，用逐小时滚动方式评估 7 天（168 小时）预测性能。

> 本文是**示例领域 Skill**。场景、字段名与设备数量都可替换；真正需要保留的是滚动评估口径、
> 模型选型硬约束与产物规范这三部分方法。

## 核心原则

**AutoGluon 是 AutoML 框架——训练和优化由它自主完成，你不要手写模型、不要手写梯度下降、不要手写训练循环。** 你的职责是准备数据、按框架接口发起训练、产出预测并评估。

你的职责是：
1. 把数据整理成 AutoGluon 要求的格式（`item_id` / `timestamp` / `target` 三列长表）
2. 调用 `TimeSeriesPredictor.fit(...)`，让 AutoGluon 完成训练与内部验证、集成
3. 用滚动方式调用 `predictor.predict(...)` 产出预测，按官方脚本评估

## 执行约束（必须严格遵守）

1. **所有产物写入 runtime 在 prompt 中给出的绝对输出目录**，不要用 `./output_combined`、`./output` 等相对路径。每设备预测 CSV（命名 `prediction*.csv`）与 `evaluation_report.json` 都必须落在该绝对目录下。
2. **同步执行，跑到完成**：不要把训练放到后台（不要 `nohup`、不要结尾 `&`、不要分离进程），必须阻塞直到 AutoGluon 训练、滚动预测、评估全部结束。
3. **结束回复前，预测 CSV 与 evaluation_report.json 必须已经写到磁盘上**。先列目录确认这些文件存在，再报告完成。
4. 不要只给出计划或"已在后台启动"就提前结束——只有预测文件真正写出后，本次运行才算完成。

## 🔴 输出铁律：每台设备必须导出独立的 prediction CSV（否则核验必然失败）

**独立核验会逐台设备查找 `prediction_<item_id>.csv`，要求真值里的每一台设备都有对应文件。少一台就判不通过——哪怕 MAPE 达标。** 这是最常见的失败原因，务必遵守：

- ✅ 真值文件（ground truth）里有多少台设备（数量以真值文件为准），就必须在输出根目录写出**同样数量**的 `prediction_<item_id>.csv`，文件名里包含该设备的完整 `item_id`（如 `prediction_ffb2af2e-8621-497c-ae85-f65804b92cea.csv`）。
- ✅ 每个预测 CSV 至少包含 `timestamp` 和 `prediction` 两列，是该设备**滚动预测得到的逐小时结果**。
- ❌ **绝对禁止**把预测只留在 AutoGluon 的 `cached_predictions.pkl` / predictor 内部缓存里就结束——那不是核验认的产物，等于没预测。必须显式 `to_csv()` 导出。
- ❌ 不要漏设备：先从真值 / 测试数据里取出所有唯一 `item_id`，**遍历每一台**做滚动预测并写 CSV；写完后 `ls` 输出目录，确认 `prediction_*.csv` 的数量 == 真值里的设备数量，数量不足禁止报告完成。
- ❌ 不要反复新建 `test_*.py` / `final_*.py` / `simple_*.py` 探索脚本把输出目录搞乱；一个脚本一次跑通：训练 → 遍历设备滚动预测 → 每台写 CSV → 评估。

**脚本末尾必须写自检 assert（按实际路径落实）：** 遍历真值里的全部 `item_id`，断言每台设备都有对应 `prediction_<item_id>.csv` 且非空、含 `timestamp`/`prediction` 两列，再断言 `evaluation_report.json` 存在。任一 assert 失败要抛异常，不许静默跳过；全部通过且 `ls` 能看到 6 个 `prediction_*.csv` 后才算完成。

## 🔴 第一铁律：必须用 Chronos t5-base 零样本（不允许换模型）

**本任务唯一被验证能达标（MAPE 13–14%）的方案是 Chronos t5-base 零样本 + `context_length=512`。这是强制要求，不是建议。**

- ✅ **必须**在 `hyperparameters` 里只用 `{"Chronos": {"model_path": "amazon/chronos-t5-base", "context_length": 512}}`。
- ❌ **禁止**使用 `DeepAR`、`TemporalFusionTransformer`、`PatchTST`、`best_quality` 或任何其他模型——实测它们在本任务（1 步滚动）上 MAPE 80%~200%+，必然不达标。
- ❌ **禁止**为了"试不同方案"而反复写多个训练脚本。一次就用 Chronos 跑通。

这是一个**短视界（`prediction_length=1`）逐小时滚动**任务。经多次实测：

| 方案 | 实测 MAPE | 结论 |
|------|-----------|------|
| **Chronos `t5-base` 零样本 + `context_length=512`** | **13–14%** | ✅ 唯一达标方案，必须用 |
| DeepAR / TemporalFusionTransformer | **200%+** | ❌ 禁用 |
| 裸 `presets="best_quality"`（不指定 Chronos） | **80%+** | ❌ 禁用 |

**标准训练代码（按此执行，不要换模型）：**

```python
predictor = TimeSeriesPredictor(
    prediction_length=1, target="target", eval_metric="MAPE", freq="h",
    path=f"{OUTPUT_DIR}/models",  # OUTPUT_DIR = runtime 在 prompt 中给出的绝对输出目录
)
predictor.fit(
    train_data,
    hyperparameters={"Chronos": {"model_path": "amazon/chronos-t5-base", "context_length": 512}},
    time_limit=600,
)
```

> 数据预处理、滚动循环、评估仍由你自主实现，但**模型必须是上面这个 Chronos 配置**。
> 如果 Chronos 权重下载失败：这是环境问题，**不要换成别的模型**（换模型只会让 MAPE 崩到 200%）。
> `HF_ENDPOINT` 已由运行环境按区域预先设置好（商业区直连 huggingface.co，中国区用 hf-mirror.com）——**不要自己覆盖 `HF_ENDPOINT`**，直接重试下载即可。若仍失败，检查网络连通性，而不是改镜像或换模型。

## 任务约束（硬约束，必须遵守）

| 约束 | 取值 | 原因 |
|------|------|------|
| 预测步长 | `prediction_length=1` | 逐小时滚动，每步只预测下一小时再回灌真值 |
| 频率 | `freq="h"` | 数据为小时级，必须显式指定，否则 AutoGluon 报"不规整频率"错误 |
| 点估计 | 分位数 `0.5`（中位数） | 列名是字符串 `"0.5"`，不是 `mean`/`median`/`q0.5` |
| 模型 | `hyperparameters={"Chronos": {"model_path":"amazon/chronos-t5-base","context_length":512}}` | 见上，决定成败的关键 |
| 评估指标 | `eval_metric="MAPE"` | 与最终评估口径一致 |
| 特征 | 仅 `target` + `item_id` + `timestamp` | 不要加任何衍生特征，评估时会缺列 |

**模型选择按上面来；除此之外的超参（训练时长内部细节等）交给 AutoGluon，不要画蛇添足、不要乱加微调参数。**

## ❌ 从实测失败中总结的 API 护栏（务必避开）

以下是真实训练任务里踩过的坑，直接导致训练失败或精度崩坏：

（模型选择的坑见上"第一铁律"，此处不重复；以下是 API/数据处理层面的坑）

| 错误写法 | 现象 | 正确做法 |
|----------|------|----------|
| `presets="chronos"` | 该 AutoGluon 版本已重命名，触发 `chronos2` 报错，训练失败 | 不用 presets 选模型，用 `hyperparameters={"Chronos": {...}}` |
| 不指定 `freq` | 时间间隔不规整 → `TimeSeriesPredictor` 报频率错误 | 创建 predictor 时传 `freq="h"` |
| 手动调 `convert_frequency(sort_by_time=...)` | 该版本无此参数，报 TypeError | 不手动转频率，靠 `resample("1h")` 聚合 |
| `context_length=128` 或更小 | 只看几天历史退化为预测均值，MAPE 100%+ | Chronos 用 `context_length=512` |
| 一次性预测 168 步 | 非因果，MAPE > 50% | `prediction_length=1` 逐步预测 + 回灌真值 |
| 取 `mean` 列作点估计 | MAPE 膨胀 | 取 `"0.5"` 列 |
| `except` 里静默填 0.0 | 掩盖错误，结果不可信 | 打印完整 traceback 并 raise |
| `pd.to_datetime(..., format="%Y-%m-%d %H:%M:%S.%f")` 写死带微秒格式 | 部分时间戳无微秒段 → `ValueError: time data ... doesn't match format`，全流程崩 | 一律用 `pd.to_datetime(..., format="mixed")`，不要写死 `%f` |
| 调 `compare_timeseries.py` 用位置参数或 `--prediction-column` | `error: the following arguments are required: --pred, --truth`，评估全失败 | 用 `--pred <csv> --truth <csv> --json-out <json>`（见"评估规则"，无 `--prediction-column`、无位置参数） |

## 数据处理

动手前先只读探查确认（不写训练代码）：
- 从真值/测试数据取出全部唯一 item_id，记下设备数量（以真值文件为准，不要写死），后续遍历以此为准
- 确认原始数据采样间隔（应为 10 秒级）、时间列可被 pd.to_datetime(format="mixed") 解析
- 确认训练期与测试期的时间边界，测试期应为 168 小时（7 天）
- 记录哪些设备有停机/全零段、近零小值密集段或离群尖峰（见下方「异常值处理」；近零小值型 MAPE 会偏高属正常，离群尖峰型可通过 winsorize 改善）

- 原始 10 秒间隔 → 按 `item_id` 分组 `resample("1h").mean()` 聚合到小时
- 列名清洗：去掉中文括号后缀；重命名为 `item_id` / `timestamp` / `target`
- `timestamp` 用 `pd.to_datetime(..., format="mixed")` 解析
- 多设备放在同一长表，用 `item_id` 区分，**训练一个多设备模型**
- 缺失小时无需填充（AutoGluon / Chronos 可处理）

## 🔴 异常值处理（提升难设备精度的关键，务必执行）

实测发现少数设备 MAPE 远高于其他（如 40%~95%），根因是两类数据异常。**处理只作用于训练/context 输入，严禁改真值、评估数据或 `compare_timeseries.py`**。

**先做诊断（探查阶段，不写训练代码）：** 对每台设备的小时级 `target` 打印：零值/近零值（`target < 1`）占比、`std/mean`（变异系数 CV）、以及原始 10 秒数据按 IQR 3 倍上界（`Q3 + 3*IQR`）判定的超界点占比。据此把设备分成三类，分别处理。

**① 离群尖峰型（训练数据有明显超界点，如某设备 max 达正常设备的 2 倍、超界点占比 > 0.5%）：**
- 在**聚合到小时之前**，对该设备的原始 10 秒 `target` 做 winsorize（分位截断）：把超过 `Q3 + 3*IQR`（或 p99.5）的值 clip 到该上界，再 `resample("1h").mean()`。这能防止极端尖峰污染小时均值和 Chronos 的 context，从而改善预测。
- 只 clip 上界离群，不平移/缩放整体分布；负值（若该指标物理上不应为负）clip 到 0。
- 这是对**训练输入**的清洗，滚动评估回灌的真值仍用未清洗的 `test_hourly_full`（评估口径不变）。

**② 近零小值型（真值中 `target < 1` 的点占比高，如 > 8%，但设备并非停机全零）：**
- 这类设备 MAPE 天然偏高——MAPE 的分母是真值，真值接近 0 时单点百分比误差会放大到数百 %，20 个小值点就能把整设备 MAPE 拉到 90%+。这是 MAPE 指标特性，**不是模型没学好，不要为压低它去动方法或改数据**。
- 能做的：确保训练 context 覆盖足够长历史（`tail(512)` 已够），让 Chronos 学到小值段的周期性；不要对小值做任何填充或抬升。
- 在评估小结里注明该设备属近零小值型、MAPE 偏高属数据特性，供人工判读。

**③ 正常型（无显著离群、近零小值占比 ~0%）：** 按常规处理，不做额外清洗。

**总平均 MAPE 预期**：做了离群 winsorize 后，尖峰型设备可从 40% 降到 20% 上下；近零小值型设备仍会偏高（属正常），因此含这类设备时总平均目标应放宽，不要为达标而对小值设备动手脚。

## 滚动评估方法（描述，非代码）

这是评估口径，不是训练逻辑——AutoGluon 负责训练，你只负责按下面的因果方式产出预测：

```
对每台设备：
  context = 该设备训练期最后 512 个小时点
  for 步 in 测试期 168 小时:
      pred = predictor.predict(context) 取 "0.5" 列第一个值并记录
      把该小时【真值】追加进 context（不是预测值，保证滚动）
      next
```

实现要点：
- context 用普通 `DataFrame` 维护，**每步预测前**再 `TimeSeriesDataFrame.from_data_frame(...)` 转换；不要直接对 `TimeSeriesDataFrame` 做 concat（会触发 MultiIndex 错误）
- 每步取 `tail(512)` 限制 context 长度
- 回灌的是 `test_hourly_full` 里的**真值**，不是 `ground_truth`，更不是预测值

## 评估规则

- MAPE 越低越好（lower_is_better）：<= target_mape（默认 20%）即达标；跨轮比较时更低才算改进
- 按设备**单独**调用 `compare_timeseries.py`，取各设备 MAPE 的算术平均
- **命令（参数名必须精确，写错会报 “the following arguments are required: --pred, --truth”）**：
  `python eval/compare_timeseries.py --pred <该设备预测csv> --truth <该设备真值csv> --json-out <该设备指标json>`
  - 只接受 `--pred` / `--truth` / `--json-out` / `--no-plot` 这几个**选项参数**；**没有** `--prediction-column`，也**不接受位置参数**（`compare_timeseries.py a.csv b.csv` 会失败）
  - 预测列由脚本自动探测（认 `target_value`/`sensor_value`/`mean`/`prediction`/`yhat` 或首个数值列），所以预测 CSV 的预测列命名为 `prediction` 即可，无需传列名参数
  - 预测 CSV 与真值 CSV 都要有 `timestamp` 列，脚本按 timestamp 对齐
  - 用 `--json-out` 让脚本把该设备指标写成 JSON，程序读它汇总，不要去解析脚本 stdout
- 不要全设备混合调用（脚本按 timestamp 合并，不含 item_id，会错配）
- **严禁修改 `compare_timeseries.py` 或原始数据**
- 正常范围：正常型设备 7–16%；离群尖峰型 winsorize 后可降到 ~20%；近零小值型/停机段设备天然偏高（40%~95% 亦属正常，是 MAPE 小分母特性，非模型问题）。含近零小值设备时总平均会被拉高，不要为达标对小值设备动手脚
- MAPE < 2% 说明数据泄漏，必须排查

## 模型环境

- 驱动 Agent 的大模型由部署方通过环境变量配置（Bedrock 托管或任意 OpenAI 兼容端点）。Skill 内容应对中等能力模型友好：用清晰的约束和护栏代替大段代码
- 训练在 g5 GPU 实例，Chronos `t5-base` 零样本约 4GB 显存
- HuggingFace 访问：`HF_ENDPOINT` 已由运行环境按区域自动配置（商业区直连 huggingface.co，中国区用 hf-mirror.com），**无需也不要手动 export/覆盖**
- 依赖：`pip install autogluon.timeseries`（>= 1.5.0）

## 性能预期

- 训练（`best_quality`，含 Chronos 零样本加载 + 其他模型）：约 5–15 分钟
- 滚动评估：30–60 分钟（168 步 × 6 设备）
- 总时间：约 60–90 分钟
