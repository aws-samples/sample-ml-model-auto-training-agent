# 已有模型直接部署（只转移文件，不训练）

算法部门已经在平台外训练好了模型（如 scikit-learn / XGBoost / LightGBM 回归模型）并写好了
推理代码。本任务**不做任何训练**，职责只有一件：**把他们的文件原样搬到部署产物目录**，让平台
后续把它打包成 `model.tar.gz` 部署到 SageMaker 端点。

## 核心原则（与其他 skill 完全不同，务必看清）

1. **绝对不要训练模型。** 不要调 AutoGluon,不要 `fit()`,不要读训练数据做建模。
2. **绝对不要修改他们的任何文件。** 不要"顺手优化"代码、不要重新序列化模型、不要改文件名、
   不要格式化代码、不要删注释。**逐字节原样复制**（用 `shutil.copy2` / `shutil.copytree`）。
   - **例外(且仅此一项)**:允许**新增**一个 `inference.py` 适配器。**新增文件 ≠ 修改他们的
     文件**——他们的代码一个字节都不动,适配器只是薄薄一层转接。见"缺 inference.py 时怎么办"。
3. **不要评估精度。** 没有真值、没有指标阈值。本任务的成功标准只有"文件齐了、契约对了"。
   **不要加载模型跑预测、不要算 MAPE/RMSE/R²**——即使输入里带了真值也不要算。这些数字会被
   误当成模型效果,而它们通常是在训练过的数据上评的(乐观偏差),有害无益。
4. 你的全部工作量应该在 40 行 Python 以内。**如果你在写建模代码,就是理解错了任务。**

## 执行约束

1. **所有产物写入 runtime 在 prompt 中给出的绝对输出目录**（`output_dir`）,不要用相对路径。
2. **同步执行,跑到完成**:不要后台运行(不要 `nohup`、结尾 `&`、分离进程)。
3. **结束回复前,`models/` 目录与 `evaluation_report.json` 必须已写到磁盘**。先 `ls -R`
   输出目录确认,再报告完成。

## 部署产物契约（这是本任务的全部要求）

平台的部署链路是这样工作的（了解它你就知道为什么要这么放）:

```
<output_dir>/models/**            ← 你要把文件放在这里
      ↓ 平台上传到 S3
      ↓ 平台把 models/ 下【所有对象】打包成 model.tar.gz（以 models/ 为压缩包根目录）
SageMaker 解压到 /opt/ml/model    ← 所以 models/ 里的相对结构会被完整保留
      ↓ 容器以 serve 模式启动
/opt/ml/model/inference.py 被加载 ← 端点从这里读取如何加载模型
```

### 必须满足

1. **所有文件放到 `<output_dir>/models/` 下**，**保持他们原有的相对目录结构，一层都不要摊平**。

   ⚠️ **最容易出错的地方**:很多包会把权重放在自己的 `model/` 子目录里,而他们的加载代码用
   的是 `SomeClass.load("model")` 这种相对路径。**这一层必须原样保留**,否则端点加载时找不到
   文件。注意平台的产物目录叫 `models/`(复数)、他们的子目录可能叫 `model/`(单数),**这是两层
   不同的目录,不要合并、不要混淆**:

   ```
   输入:  legacy_model.py  features.py  model/lgbm_seed0.txt  model/preprocess.json
                                    ^^^^^ 他们自己的子目录

   产出:  <output_dir>/models/legacy_model.py
          <output_dir>/models/features.py
          <output_dir>/models/model/lgbm_seed0.txt      ← model/ 这层保留
          <output_dir>/models/model/preprocess.json

   端点:  /opt/ml/model/legacy_model.py                     ← models/ 成为 tar 根
          /opt/ml/model/model/lgbm_seed0.txt            ← 所以这里是 model/model/... 的形态
   ```

   平台打包时以 `models/` 为压缩包根目录解压到 `/opt/ml/model`,所以 `models/` 这一层会消失、
   他们的 `model/` 子目录会保留。适配器里就用 `os.path.join(model_dir, "model")` 定位。
2. **`inference.py` 必须位于 `models/` 根目录或 `models/code/` 下**（两个位置都支持）。
   平台的 `serve.py` 按这个顺序查找:`inference.py` → `code/inference.py`。
3. **`inference.py` 必须定义 `model_fn(model_dir)`**,返回加载好的模型对象。
   - 可选定义 `predict_fn(data, model)`；不定义时端点会退化为
     `model.predict(pandas.DataFrame(instances))`。**只要他们的入口不是"接受 DataFrame 的
     .predict()",就必须提供 `predict_fn`**。
   - `input_fn` / `output_fn` **不会被调用**（端点固定使用平台的 JSON 信封），
     如果他们的代码依赖这两个函数,要在报告里**明确指出**。
4. **至少要有一个模型权重文件**（`.joblib` / `.pkl` / `.json` / `.ubj` / `.txt` / `.sav` 等,
   名字随他们）。只有代码没有权重是不完整的。

### 严禁

- 不要把训练数据、`__pycache__`、`.git`、`.ipynb_checkpoints`、`.DS_Store`、`*.pyc`
  复制进 `models/`。这些会白白增大 `model.tar.gz`、拖慢端点冷启动。
- **不要复制输入里的 `model.tar.gz`**。那是上一次部署的产物被回传了；把它搬进 `models/` 会被
  再打包一次,形成"tar 套 tar",**每搬运一次体积翻一倍**。平台会自己生成新的 `model.tar.gz`。
- 不要在 `models/` 里放 `prediction*.csv` 这类文件——平台的产物识别逻辑会跳过含
  "prediction" 的文件名,放了不影响功能但会造成混淆。
- **示例数据（如 `examples/`）默认不要复制**。它们可能很大（几百 KB 到几 MB）且端点不需要。
  若他们的代码启动时会读示例文件（少见）,则必须保留,并在报告里说明原因。

## 执行步骤

1. **列出输入目录**:`os.walk(input_dir)` 打印完整文件清单（相对路径 + 字节数）。
   这份清单要写进报告,是搬运正确性的证据。
2. **读文档**:优先读 `README*` / `demo*.py` / `example*` —— 它们通常直接写明了入口类/函数、
   加载参数、`predict()` 的参数名和返回字段。**这一步决定后面能不能写适配器,不要跳过。**
3. **确定推理入口**:
   - 输入里已有 `inference.py`（递归找,多个则取路径最浅的,并在报告里列出全部候选）→ 直接用。
   - 没有 `inference.py`,但文档明确了入口 → 走"缺 inference.py 时怎么办"的**情况 A**,
     准备新增适配器。
   - 识别不出 → 走**情况 B**,照常搬运并在 `blocking_issues` 写明。
4. **原样复制**到 `<output_dir>/models/`,**保持相对结构一层不动**,按"严禁"清单过滤。
   - 唯一允许移动文件的情形:输入里已有的 `inference.py` 位于某个子目录（如 `src/inference.py`）
     且平台只在根目录/`code/` 下查找,此时**把它提升到 `models/` 根目录**,同时确保它引用权重
     的相对路径依然正确。必须在报告的 `path_adjustments` 里写明调整了什么、为什么。
   - **其他任何文件都不要移动**——尤其不要把他们 `model/` 子目录里的权重摊平到根目录。
5. **若走情况 A：写入适配器** `<output_dir>/models/inference.py`（模板见下文）。参数名、
   加载路径**照抄文档/源码里的真实签名**,不要凭印象编。
6. **静态校验 `inference.py`**（不要执行它,见下方"为什么不执行 model_fn"）:
   - 确认包含 `def model_fn`；记录是否有 `def predict_fn` / `def input_fn` / `def output_fn`。
   - 若是你新增的适配器,额外用 `ast.parse()` 确认语法正确（不执行代码）。
7. **记录依赖线索**（对排查版本问题极其重要）:
   - 如果输入里有 `requirements.txt` / `environment.yml` / `pyproject.toml`,把内容
     **原文抄进报告**。**不要 pip install 任何东西**（端点用的是镜像里已装的版本）。
   - 从推理入口及其引用的模块里提取所有 `import` 语句,列进报告。
   - 打印当前镜像里这些库的版本（能 import 到的就打印 `__version__`）,和他们的声明
     **并列**放在报告里,供人工比对。
8. **写 `evaluation_report.json`**（见下,含 `endpoint_contract` 一节）。
9. **自检 + `ls -R`** 后报告完成。

### 为什么不执行 model_fn

**不要在训练容器里调 `model_fn` 试加载。** 训练容器和端点容器虽同一镜像,但训练是 root、
端点是非 root 用户(uid 1000),`/opt/ml/model` 也还不存在——你的产物这时还在 `output_dir` 里,
路径与端点完全不同。在这里"验证成功"不代表端点能成功,反而可能因为路径不同而误判。
**加载验证由端点的 `/ping` 健康检查负责**——那才是真实环境。

**允许且推荐的一项轻量检查**(仅当你新增了适配器时):用 `ast.parse()` 静态解析产出的
`models/inference.py`,确认语法正确、且定义了 `model_fn`。`ast.parse` **不执行代码**,能挡住
适配器写错语法这种低级错误。除此之外一律只做"文件存在 + 文本包含"这类不执行代码的检查。

## 缺 inference.py 时怎么办

大多数算法部门交付的包**都不会**正好叫 `inference.py`——他们通常提供一个类或函数入口
（如 `legacy_model.py` 里的 `LegacyModel.load()` / `predict()`）。这是**正常情况,不是错误**。

### 情况 A：能明确识别出入口 → 新增一个薄适配器（首选）

判据（三条**全部**满足才算"能明确识别"）:

1. 有 README / 文档 / `demo.py` 之类的示例,**明确写出了怎么加载、怎么调用**;
2. 加载入口的**参数含义明确**（知道该把哪个目录传进去）;
3. 预测入口的**参数名和返回值明确**。

满足则**新增** `<output_dir>/models/inference.py`，内容只做转接，**不要重新实现任何逻辑**:

```python
"""平台契约适配器：把 <他们的入口> 接到 model_fn / predict_fn 上。
不改算法部门任何文件，只新增这一层薄封装。"""
import os
from <他们的模块> import <他们的类>

# 他们 predict() 接受的参数名，照抄他们的签名/文档，不要自己编
_KWARGS = ("参数1", "参数2", ...)

def model_fn(model_dir):
    # 若权重在他们自己的子目录里，这里要拼上那一层
    return <他们的类>.load(os.path.join(model_dir, "<他们的子目录>"))

def predict_fn(data, model):
    out = []
    for row in data:
        kw = {k: row[k] for k in _KWARGS if k in row}
        out.append(model.predict(**kw))
    return out
```

关于 `predict_fn` 的返回:
- 他们的 `predict()` 返回 **dict** → 直接放进 list 返回。平台会把 dict **原样透传**给调用方,
  所有字段（置信度、误差带、warnings 等）都会保留,**不要自己精简或改名**。
- 返回**标量** → 也放进 list,平台会包成 `{"prediction": <值>}`。

写完适配器后,在报告里:`inference_entrypoint` 填 `"inference.py (adapter)"`,并在
`path_adjustments` 里写明"新增适配器,转接 `<他们的入口>`；未修改任何原文件"。

**只用 `_KWARGS` 白名单挑参数**（`if k in row`）,不要把整行 `**row` 展开——否则调用方多传
一个字段（如示例数据里的 `desc`、`actual_duration_min`）就会 `TypeError`。

### 情况 B：识别不出入口 → 照常搬运，报告里写明

不满足上面三条判据（没文档、参数含义不明、有多个候选入口分不清）时,**不要靠猜写适配器**——
猜错的适配器比没有更糟,它会让端点以难以诊断的方式失败。

此时:原样复制全部文件,`inference_entrypoint` 填 `null`,在 `blocking_issues` 里写明
"未找到 `inference.py`,候选入口为 `<列出全部候选>`,参数契约不明确,需算法部门提供
`inference.py`（定义 `model_fn(model_dir)`）或补充调用文档"。

**两种情况都要照常完成搬运并写报告**,不要因为缺文件就什么都不产出——人工补一个文件重跑即可,
不用从头来。

## evaluation_report.json 内容

```json
{
  "task": "byo_model_deploy",
  "mode": "file_transfer_only",
  "trained": false,
  "input_inventory": [{"path": "inference.py", "bytes": 1234}],
  "copied_to_models": [{"path": "inference.py", "bytes": 1234}],
  "skipped": [{"path": "__pycache__/x.pyc", "reason": "bytecode"}],
  "path_adjustments": [],
  "inference_entrypoint": "inference.py (adapter)",
  "adapter_added": true,
  "adapter_wraps": "legacy_model.LegacyModel.load / .predict",
  "handler_functions": {"model_fn": true, "predict_fn": true,
                        "input_fn": false, "output_fn": false},
  "endpoint_contract": {
    "instance_fields": ["line_id", "cutoff_ts", "planned_ignition_ts",
                        "temp[]", "tar[]", "air[]", "prod[]", "recent_runs[]"],
    "approx_bytes_per_instance": 91000,
    "suggested_max_instances_per_request": 60,
    "example_request": {"instances": [{"line_id": "line-02", "temp": "[...]"}]}
  },
  "declared_dependencies": "<requirements.txt 原文，没有则 null>",
  "imports_in_inference": ["joblib", "pandas", "sklearn"],
  "image_library_versions": {"scikit-learn": "1.7.2", "numpy": "2.3.5"},
  "blocking_issues": [],
  "total_bytes_copied": 123456
}
```

`blocking_issues` 为空数组表示契约齐全、可以部署；非空表示需要人工介入。**即使非空也要把
报告写出来**,这是给人看的诊断依据。

## 自检（脚本末尾必须做）

用 `assert`:

1. `<output_dir>/models/` 存在且非空;
2. `models/inference.py` 或 `models/code/inference.py` 存在（原有的或你新增的适配器。若属
   "情况 B" 确实无法提供,允许这条不通过,但必须已在 `blocking_issues` 写明,且**其余产物都已
   落盘**）;
3. `models/` 下至少有一个**非 `.py`** 文件（即模型权重存在）;
4. **目录层级校验**:对输入里每个非排除文件,断言它在 `models/` 下的**相对路径与输入完全一致**
   （即 `model/lgbm_seed0.txt` 仍在 `models/model/` 下,没被摊平）。这条能挡住最常见的搬运错误;
5. 若新增了适配器:断言它 `import` 的模块文件确实存在于 `models/` 下（如
   `from legacy_model import LegacyModel` → `models/legacy_model.py` 必须存在）;
6. `evaluation_report.json` 存在且能被 `json.load` 解析;
7. `models/` 下**不含** `__pycache__` / `*.pyc` / `.git` / `model.tar.gz`;
8. 报告里**不含** `prediction_results` / `MAPE` / `RMSE` / `R2` 之类的评估字段——本任务不评估。

全部通过并 `ls -R` 确认后才报告完成。

## 版本兼容性提醒（写进报告，不要试图自己解决）

先判断权重是**哪类格式**,风险差别很大:

| 格式 | 例子 | 跨版本风险 |
|---|---|---|
| **框架原生** | LightGBM `.txt`、XGBoost `.json`/`.ubj`、纯 JSON 参数 | **低**。这类格式设计上就是跨版本稳定的,只要满足 `requirements.txt` 里的下界即可 |
| **pickle** | `joblib.dump` / `pickle.dump` 出的 `.pkl` / `.joblib` / `.sav` | **高**。尤其 numpy 1.x → 2.x、scikit-learn 跨小版本,可能报错或**静默给出错误结果** |

**在报告里明确写出是哪一类**。若是原生格式,只需比对版本下界是否满足;若是 pickle,要重点标注
风险。你**无法在训练容器里可靠验证**这一点（见"为什么不执行 model_fn"）。

你要做的只是**把双方版本并列写进报告**（步骤 5）,让人能一眼看出差异。**不要**为了"修好它"
去 pip install、降级、或重新序列化模型——那会破坏"原样搬运"这个前提,而且端点用的是镜像里
的版本,训练容器里装什么都不影响端点。

真出现版本不兼容,端点会在 `/ping` 阶段报加载失败,那时按报告里的版本对照表调整镜像即可。

## 推理端点契约（写进报告，供上线对接）

部署后端点接受平台统一的 JSON 信封,**每个 instance 就是一次 `predict()` 调用的参数集**:

```json
{"instances": [ {"参数1": ..., "参数2": ...}, ... ]}
```

要点（请在报告里如实写清,这是调用方对接的唯一依据）:

- **字段值不限于标量**。可以是**嵌套数组**（如分钟级时序 `temp` / `tar`）或**对象列表**
  （如 `recent_runs`）——平台的推理链路对这些值是**原样透传**,不做任何转换或特征工程。
- **调用方必须自己把所有参数准备好**。平台**不会**替你算派生特征、不会去取历史数据。
- **体积上限**:SageMaker `invoke_endpoint` 单次请求上限 **6 MB**。如果单个 instance 很大
  （例如含 2881 点的分钟级序列约 90 KB）,**一次请求的条数要相应控制**（90 KB × 60 ≈ 5.4 MB
  已接近上限）。请在报告里给出"单 instance 估算体积"和"建议单次最大条数"。
- **返回**:他们 `predict()` 返回的 dict 会被原样透传,字段一个不少。

在报告里用**他们示例数据里的真实字段**举一个最小请求示例（长数组用 `[...]` 省略即可),
不要凭空编字段名。

## 定位提醒

这是**搬运 + 一层薄适配**的任务,不是建模任务。成功标准:`models/` 目录结构正确（**他们自己的
子目录层级保留**）、`inference.py` 契约满足（原有的或新增的适配器）、报告写清了文件清单、
版本对照、推理契约。**不要训练、不要评估精度、不要改他们的代码。**
