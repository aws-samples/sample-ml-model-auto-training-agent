# 软件物料清单 (Software Bill of Materials, SBOM)

- **项目**:智能体驱动的模型训练平台 (auto-training-agent)
- **项目许可证**:MIT-0(MIT No Attribution,见 LICENSE)
- **版本**:PoC
- **生成日期**:2026-07-24
- **说明**:本清单枚举本项目直接依赖的开源组件及其许可证。传递依赖随各包管理器解析,许可证以对应上游为准;交付时可用 `pip-licenses` / `license-checker` 生成完整传递依赖清单。

## 一、基础设施 / 后端 (Python)

来源:`src/requirements.txt`(CDK)、`src/training_runtime/requirements.txt`(训练运行时)、Lambda 运行时依赖。

| 组件 | 用途 | 许可证 |
|---|---|---|
| aws-cdk-lib (>=2.242.0,<3) | 基础设施即代码 | Apache-2.0 |
| constructs (>=10.5.0,<11) | CDK 构造基类 | Apache-2.0 |
| boto3 / botocore | AWS SDK(Lambda + 运行时) | Apache-2.0 |
| awscli | 训练容器内 AWS CLI | Apache-2.0 |
| strands-agents / strands-agents-tools | 训练 Agent 框架 | Apache-2.0 |
| autogluon (autogluon.timeseries) | 自动化 ML / 时序预测 | Apache-2.0 |
| torch / torchvision (2.7.1+cu118 / 0.22.1+cu118) | 深度学习框架(CUDA 11.8) | BSD-3-Clause |
| nvidia-*-cu11 系列 | CUDA 运行时库 | NVIDIA Proprietary(随 PyTorch 分发,仅运行时使用) |
| triton (3.3.1) | GPU kernel 编译 | MIT |
| transformers / huggingface_hub | Chronos 模型加载 | Apache-2.0 |
| chronos (amazon/chronos-t5-base 模型) | 时序预测预训练模型 | Apache-2.0 |
| pandas / numpy | 数据处理 | BSD-3-Clause |

## 二、前端 (Node/TypeScript)

来源:`src/web/package.json`。

| 组件 | 用途 | 许可证 |
|---|---|---|
| react / react-dom (^18) | UI 框架 | MIT |
| typescript | 类型系统 | Apache-2.0 |
| vite / @vitejs/plugin-react | 构建工具 | MIT |
| vitest | 测试框架 | MIT |
| @testing-library/react / jest-dom / user-event | 测试工具 | MIT |
| fast-check | 属性测试 | MIT |
| jsdom | 测试 DOM 环境 | MIT |
| @types/react / @types/react-dom | 类型定义 | MIT |

## 三、容器基础镜像

| 镜像 | 用途 | 许可证 |
|---|---|---|
| public.ecr.aws/docker/library/python:3.12-slim | 训练镜像基础镜像 | PSF(Python)+ Debian 组件各自许可 |

## 四、AI/ML 模型许可

- **amazon/chronos-t5-base**:零样本时序预测模型,**Apache-2.0**。构建时预下载并烤入训练镜像,推理时离线加载。

## 五、许可证合规说明

- 直接依赖均为 **Apache-2.0 / MIT / BSD-3-Clause** 等宽松许可,通常在企业批准清单内。
- nvidia-*-cu11 为 NVIDIA 专有许可,仅作为 PyTorch CUDA 运行时随镜像使用,不修改、不再分发源码。
- **交付前须**:① 用 `pip-licenses`(Python)与 `license-checker`(npm)生成完整传递依赖许可清单;② 对照公司批准的开源许可清单核验;③ 确认无 GPL/AGPL 等 copyleft 许可混入。
