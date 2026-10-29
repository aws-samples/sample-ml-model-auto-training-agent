# ml-model-auto-training-agent

A serverless, AI-agent-driven platform for automated time-series model training on AWS. Business users submit a training task from a web console; an LLM-powered agent (Strands Agent SDK) autonomously explores the data, writes and runs AutoGluon/Chronos training code on a GPU, self-verifies the results, and publishes the model to a SageMaker real-time endpoint — no ML engineer in the loop.

Deploys to both AWS commercial regions (us-west-2) and AWS China regions (cn-north-1 / cn-northwest-1) from a single CDK codebase.

The two training task types that ship with the repo (`timeseries_forecast`, `tabular_regression`) are
**illustrative examples**: they show what a domain skill looks like and how the task-type registry works.
Replace them with your own scenarios — no platform code changes required.

## Background

Teams that need recurring forecasts over industrial sensor time series usually hit the same bottleneck:
retraining a model for every new scenario requires scarce ML expertise. This project removes that bottleneck:

- **Autonomous training**: an agent runs the full train–evaluate–retry loop inside a GPU container, guided by Markdown "skills" (SOPs) rather than hard-coded pipelines.
- **Trust but verify**: a deterministic Runner independently re-computes evaluation metrics from ground truth (never trusting the agent's self-reported numbers) before accepting a model.
- **New scenario = new skill file**: extending to another forecasting domain only requires one entry in
  `cdk.json` `taskTypes` plus a new domain skill in Markdown; no Python changes.

## Architecture

```
Browser (React SPA)
   │  commercial: CloudFront + S3, token auth
   │  China: self-hosted frontend, SigV4-signed direct calls
   ▼
API Gateway (REST, request validation, access logs)
   ▼
Lambda control plane (11 functions, VPC private subnets)
   │  tasks / data / images / models / inference / auth / notifications
   ├─► DynamoDB ×6 (tasks, models, infer-tasks, users, images, messages)
   ├─► S3 data bucket (data / output / logs prefixes)
   ├─► AWS Batch (g5 GPU, minvCpus=0) ──────────► training container
   │        agent: Strands SDK + Bedrock or any OpenAI-compatible endpoint
   │        training: AutoGluon TimeSeries + Chronos (weights baked in image)
   ├─► CodeBuild (builds agent image, pushes to ECR; non-root serve variant)
   ├─► SageMaker real-time endpoint (GPU, deploys verified models)
   └─► EventBridge (Batch/CodeBuild state changes → status reconciliation)
```

Key design decisions:

- **Deterministic guardrails around a probabilistic core** — the LLM agent decides *how* to train; deterministic code owns contracts, concurrency, verification, artifact publishing and lifecycle.
- **Single stack, dual region** — `is_china` branching switches auth mode (Lambda token authorizer vs. AWS_IAM SigV4), frontend hosting (CloudFront vs. self-hosted), package mirrors (PyPI/PyTorch/HuggingFace vs. Aliyun/hf-mirror), and S3 public-access handling (SCP constraints).
- **One image, two roles** — the same container image trains on Batch (root, default stage) and serves on SageMaker (non-root `-serve` stage); the endpoint prefers the non-root variant automatically.

## Repository layout

```
src/
├── app.py, cdk.json           # CDK entry point + context (all feature/security toggles)
├── infra/infra_stack.py       # The single CDK stack (region-aware)
├── lambda/                    # Control-plane Lambdas (Python 3.14, stdlib + boto3 only)
│   ├── api/                   # Task submit/list/cancel/deploy, logs, evaluation, messages
│   ├── auth_users/            # Login + PBKDF2 password hashing, account seeding
│   ├── authorizer/            # Token authorizer (commercial regions only)
│   ├── data_list/ data_upload/# S3 data browser, presigned upload, base64 download
│   ├── image_list/ image_build/ image_oncomplete/   # Agent-image build orchestration
│   ├── deployment/            # SageMaker model packaging + endpoint create/update
│   ├── infer/                 # Inference task management (invoke endpoint)
│   ├── oncomplete/            # Batch job state reconciliation
│   └── shared/                # Logging (level filter + secret masking), responses, utils
├── training_runtime/          # Code baked into the training container
│   ├── Dockerfile             # Multi-stage: training (root) / serve (non-root, SageMaker)
│   ├── main.py, runner.py     # Attempt loop, independent metric verification
│   ├── serve.py               # SageMaker inference server (stdlib HTTP)
│   └── skills/                # Agent SOPs: general spec + domain skills (Markdown)
├── web/                       # React + Vite SPA (task wizard, data/image/model consoles)
└── tests/                     # pytest: unit (incl. CDK template assertions), contracts
```

## Deployment

Prerequisites: Node.js 20.19+ or 22.12+ (required by Vite 8), Python 3.12+, an AWS account bootstrapped for CDK (`npx cdk bootstrap`).

```bash
# 1. Install dependencies
cd src
python -m venv ../.venv && source ../.venv/bin/activate
pip install -r requirements.txt

# 2. Review configuration in cdk.json
#    - seedAccounts: login accounts. Empty by default, so no account exists and
#      nobody can sign in until you supply one. Pass them at deploy time (step 3)
#      instead of committing passwords to cdk.json.
#    - enableXray, s3Lifecycle, apiLogRetentionDays, lambdaLogLevel: baseline, tunable
#    - Optional security toggles (default OFF): enableApiWaf, enableVpcFlowLogs,
#      dynamodbSecurity.{pointInTimeRecovery,cloudtrailDataEvents,configRules},
#      enableAsyncFailureQueue, ecrImmutableTags (immutable image tags; recommended
#      for production — then build each image under a new tag)

#    - llm: the model that drives the training agent. Either Amazon Bedrock
#      (uses the Batch job role, no API key):
#        -c llm='{"provider":"bedrock","modelId":"us.anthropic.claude-sonnet-4-5-20250929-v1:0"}'
#      or any OpenAI-compatible endpoint (default provider "openai"); put the
#      API key into the Secrets Manager secret auto-training-agent-provider-api-key:
#        -c llm='{"baseUrl":"https://<endpoint>/v1","modelId":"<model>"}'

# 3. Deploy (the region determines the branch automatically)
SEED='[{"username":"admin","password":"<strong-password>","role":"admin"}]'
CDK_DEFAULT_ACCOUNT=<account> CDK_DEFAULT_REGION=us-west-2 npx cdk deploy -c seedAccounts="$SEED"   # commercial
CDK_DEFAULT_ACCOUNT=<account> CDK_DEFAULT_REGION=cn-north-1 npx cdk deploy -c seedAccounts="$SEED"  # China

# 4. Build the first training image (from the web console, or start the
#    CodeBuild project directly). China builds automatically use Aliyun
#    PyPI/PyTorch mirrors and hf-mirror for the Chronos weights.

# 5. Frontend
#    - Commercial: cd web && npm ci && npm run build; upload dist/ to the frontend
#      bucket (runtime-config.js comes from stack outputs), served via CloudFront.
#    - China: self-host dist/ on an internal server (e.g. Nginx); fill
#      runtime-config.js with ApiBaseUrl / FrontendSignHost / FrontendSignRegion /
#      access keys from the stack outputs. Never commit real credentials.
```

The CDK stack in `src/infra/infra_stack.py` is the authoritative description of what gets
created; every non-obvious decision is documented in comments next to the code it affects.

## Testing

```bash
cd src && python -m pytest tests/unit -q     # handlers, adapters, CDK template assertions
cd src/web && npm test                        # frontend (vitest)
```

## Security

Highlights:

- Least-privilege IAM per Lambda (11 functions, 11 distinct roles); `iam:PassRole` pinned to exact ARNs.
- Secrets live in Secrets Manager only; structured logging automatically masks token/password/credential fields; API access logs carry request metadata only, with 90-day retention.
- All buckets encrypted and SSL-enforced, with server access logging and lifecycle rules; API Gateway request validation on every business POST method.
- Opt-in hardening via cdk.json (default off): AWS WAF, VPC Flow Logs, DynamoDB PITR / CloudTrail data events / AWS Config rules, and an SQS failure queue for async Lambda invocations.

## License

This project is licensed under the MIT No Attribution License (MIT-0) — see [LICENSE](LICENSE).
Every source file carries `SPDX-License-Identifier: MIT-0`.

Third-party components and their licenses are listed in [NOTICE](NOTICE); the dependency inventory is in [SBOM.md](SBOM.md).
The resolved dependency tree contains **no GPL, LGPL or AGPL component**.

Two things to be aware of before you redistribute anything you build:

- **No prebuilt container image is published, on purpose.** Building the training
  image installs NVIDIA CUDA runtime wheels (`nvidia-cudnn-cu11` and friends),
  which are under **NVIDIA proprietary terms**, not an open-source license.
  Installing them for your own use is covered by the applicable NVIDIA license
  agreement, but pushing a built image to a public registry is a separate act of
  redistribution governed by those terms. See NOTICE section 5.1.
- **Pretrained model weights carry their own license.** A time-series foundation
  model checkpoint is optionally baked into the image at build time
  (`CHRONOS_MODEL_ID` in `src/training_runtime/Dockerfile`). Check the model card
  of whichever checkpoint you configure. See NOTICE section 5.2.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). This project has adopted the
[Amazon Open Source Code of Conduct](https://aws.github.io/code-of-conduct)
— see [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

Please report security issues via the
[AWS vulnerability reporting page](http://aws.amazon.com/security/vulnerability-reporting/)
rather than a public GitHub issue.
