# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# Multi-stage image:
#  - "training": runs as root. AWS Batch training needs root for /opt/ml
#    scratch paths, agent shell tools and workspace writes. Build it with
#    --target training: it is NOT the default, docker builds the last stage.
#  - "serve": same image plus a non-root user; this is the variant deployed to
#    SageMaker endpoints (security baseline: no root in deployed containers).
#    CodeBuild pushes it as the companion tag "<tag>-serve".
# Use the ECR Public mirror of the official Python image to avoid Docker Hub
# anonymous pull rate limits (HTTP 429) in CodeBuild's shared egress.
#
# Pinned by digest (multi-arch index of python:3.12-slim as of 2026-10-28) so a
# rebuild cannot silently pick up a different base image. To take base-image
# security patches, resolve the current digest of the tag and bump it here:
#   docker buildx imagetools inspect public.ecr.aws/docker/library/python:3.12-slim
FROM public.ecr.aws/docker/library/python:3.12-slim@sha256:6b25cbe6577518889d553cdd5a5da97d259348d8f6e45b527dd731caaba27a07 AS training

# PyPI index is region-dependent and passed at build time:
#  - Commercial regions (default): the official PyPI (pypi.org) is fastest.
#  - China: pass --build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
#    (+ PIP_TRUSTED_HOST=mirrors.aliyun.com), because pypi.org is unreachable/slow
#    there. Hardcoding the Aliyun mirror previously made commercial-region builds
#    crawl at ~500 kB/s and time out downloading torch/CUDA/etc.
ARG PIP_INDEX_URL=https://pypi.org/simple/
# Empty by default: --trusted-host makes pip skip TLS certificate verification
# for that host, so it must never be on for the public index. Pass it as a build
# arg only for a mirror that genuinely needs it (the China build does). pip
# treats an empty PIP_TRUSTED_HOST as "no trusted hosts". Build-time only: an
# ARG is visible to the RUN steps below but, unlike ENV, does not persist into
# the image, so containers never run with TLS verification relaxed.
ARG PIP_TRUSTED_HOST=

# The CUDA 11.8 torch/torchvision + nvidia-*-cu11 wheels come from a dedicated
# PyTorch wheel source (not the normal PyPI index), region-dependent:
#  - Commercial regions (default): PyTorch's official PEP503 index
#    (download.pytorch.org), consumed via --index-url TORCH_INDEX_URL.
#  - China: download.pytorch.org (Cloudflare R2 / download-r2.pytorch.org) is
#    unreachable/times out — this multi-GB layer is what fails the China build
#    ("download-r2.pytorch.org: Read timed out"). NOTE: PEP503 *index* mirrors
#    like mirror.sjtu.edu.cn only proxy the index page; their wheel links still
#    point back to download-r2.pytorch.org, so they DON'T help. The Aliyun
#    pytorch-wheels mirror instead HOSTS the wheels locally (flat directory, no
#    PEP503), so China must consume it via --find-links, not --index-url:
#      --build-arg TORCH_FIND_LINKS=https://mirrors.aliyun.com/pytorch-wheels/cu118/
#    torch/torchvision/nvidia-*-cu11 resolve from that flat dir; triton (which is
#    NOT in the pytorch-wheels mirror) resolves from PIP_INDEX_URL (Aliyun PyPI).
# TORCH_INDEX_URL is used only when TORCH_FIND_LINKS is empty (commercial).
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cu118
ARG TORCH_FIND_LINKS=

# HuggingFace endpoint used to PRE-BAKE the Chronos weights into the image at
# build time (region-aware: huggingface.co for commercial, hf-mirror.com for
# China). The Chronos t5-base model is zero-shot — its weights are NOT stored in
# the AutoGluon predictor (model.tar.gz is only ~180KB of metadata) and are
# normally downloaded from HF at PREDICT time. On a SageMaker real-time endpoint
# that runtime download is unreliable (egress/timeout), which surfaced as the
# opaque "Following models failed to predict: ['Chronos[...]']". Baking the
# weights into the image lets both training and inference load Chronos OFFLINE.
ARG HF_ENDPOINT=https://huggingface.co
ARG CHRONOS_MODEL_ID=amazon/chronos-t5-base

# HF_HOME points at an in-image cache directory that persists in the image
# layers, so the pre-baked weights are present at runtime without any network.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    WORKSPACE_ROOT=/work/runtime \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_DEFAULT_TIMEOUT=300 \
    PIP_RETRIES=10 \
    HF_ENDPOINT=${HF_ENDPOINT} \
    HF_HOME=/opt/hf-cache

WORKDIR /work

# System libraries required by ML wheels at RUNTIME (the python:3.12-slim base
# omits them). LightGBM (which AutoGluon frequently selects as the best model)
# links against OpenMP's libgomp.so.1; without it the model trains fine but the
# SERVE variant crashes on `import lightgbm` when the endpoint loads the saved
# model — "OSError: libgomp.so.1: cannot open shared object file". Installed in
# the training stage so both training and the serve stage inherit it.
#
# APT_MIRROR redirects Debian's package host, region-dependent like the pip and
# torch indexes above: deb.debian.org is unreachable from China's CodeBuild
# egress (a build stalled 457s then failed "Unable to connect to
# deb.debian.org"), so China passes
#   --build-arg APT_MIRROR=http://mirrors.aliyun.com
# Empty (commercial default) leaves the stock sources untouched.
# Debian 12+/13 ships deb822 sources at /etc/apt/sources.list.d/debian.sources;
# older layouts use /etc/apt/sources.list — rewrite whichever exists. One mirror
# base covers both entries, because the paths line up:
#   http://deb.debian.org/debian            -> <mirror>/debian
#   http://security.debian.org/debian-security -> <mirror>/debian-security
# Plain http avoids needing ca-certificates this early in the build.
ARG APT_MIRROR=
RUN set -eu; \
    if [ -n "${APT_MIRROR}" ]; then \
      for f in /etc/apt/sources.list.d/debian.sources /etc/apt/sources.list; do \
        [ -f "$f" ] || continue; \
        sed -i \
          -e "s|https\?://deb.debian.org|${APT_MIRROR}|g" \
          -e "s|https\?://security.debian.org|${APT_MIRROR}|g" \
          -e "s|https\?://archive.debian.org|${APT_MIRROR}|g" "$f"; \
        echo "Rewrote $f to use ${APT_MIRROR}"; \
      done; \
    fi; \
    apt-get -o Acquire::Retries=3 update \
    && apt-get -o Acquire::Retries=3 install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && ldconfig -p | grep -q libgomp.so.1

# Optional: trust an internal / private CA. Drop your organization's root CA
# certificate (*.pem or *.crt) into training_runtime/certs/ and it is installed
# here, so the training agent can call an internal HTTPS model endpoint with TLS
# verification ON instead of turning verification off.
#
# Two things are required:
#  1. update-ca-certificates -> OS trust store (/etc/ssl/certs). `update-ca-
#     certificates` only accepts *.crt, so *.pem inputs are normalized to *.crt.
#  2. certifi bundle + SSL_CERT_FILE / REQUESTS_CA_BUNDLE / CURL_CA_BUNDLE — the
#     agent's HTTP stack (openai SDK -> httpx, requests, urllib, curl) defaults
#     to certifi and ignores the OS store, so the CA is appended there too and
#     the three env vars point at the merged bundle.
#
# The certs/ directory always exists (ships a README), so this COPY never fails;
# with no *.pem/*.crt the step is a no-op and the image is unchanged. Placed
# before the pip layers so an internal package mirror behind the same CA works.
COPY training_runtime/certs/ /tmp/internal-ca/
RUN set -eu; \
    # `find` (not a glob) so a missing .pem OR .crt extension does not make the
    # test fail — a single root CA file is the common case.
    ca_count="$(find /tmp/internal-ca -maxdepth 1 -type f \( -name '*.pem' -o -name '*.crt' \) | wc -l)"; \
    if [ "${ca_count}" -gt 0 ]; then \
      apt-get update && apt-get install -y --no-install-recommends ca-certificates; \
      mkdir -p /usr/local/share/ca-certificates/corp-internal; \
      # update-ca-certificates only accepts *.crt, so normalize *.pem -> *.crt.
      find /tmp/internal-ca -maxdepth 1 -type f \( -name '*.pem' -o -name '*.crt' \) \
        -exec sh -c 'cp "$1" "/usr/local/share/ca-certificates/corp-internal/$(basename "${1%.*}").crt"' _ {} \; ; \
      update-ca-certificates; \
      rm -rf /var/lib/apt/lists/*; \
      CERTIFI_PEM="$(python -c 'import certifi; print(certifi.where())' 2>/dev/null || true)"; \
      if [ -n "${CERTIFI_PEM}" ] && [ -f "${CERTIFI_PEM}" ]; then \
        for crt in /usr/local/share/ca-certificates/corp-internal/*.crt; do \
          cat "${crt}" >> "${CERTIFI_PEM}"; \
        done; \
        echo "Appended internal CA to certifi bundle at ${CERTIFI_PEM}."; \
      fi; \
      echo "Installed ${ca_count} internal CA certificate(s) into the trust store."; \
    else \
      echo "No internal CA provided (certs/ has no *.pem/*.crt); skipping."; \
    fi; \
    rm -rf /tmp/internal-ca

# Point every HTTP client at the OS trust store (now containing the internal
# CA): requests, httpx/openai SDK, urllib and curl each read one of these.
# Harmless when no internal CA was added — the OS bundle already holds the
# public roots, so public endpoints (pip/HF mirrors) keep working.
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    CURL_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

# Install the CUDA 11.8 torch/torchvision (+ nvidia-*-cu11 runtime) FIRST, from
# the dedicated PyTorch wheel index. Doing this BEFORE autogluon is both a
# correctness and a performance fix:
#  - Correctness: autogluon depends on torch<2.10,>=2.6 and would otherwise pull
#    its default cu128 (CUDA 12.8) build, which fails on the SageMaker real-time
#    endpoint — its g5 (A10G) instances ship NVIDIA driver 470.x (CUDA 11.4),
#    too old for cu128 ("driver is too old" → Chronos "no GPU detected"). cu118
#    (min driver 450) works with BOTH the endpoint's 470 driver and the newer
#    Batch training driver (580).
#  - Performance: pre-satisfying torch 2.7.1+cu118 means the later autogluon
#    install no longer downloads the ~2.5GB cu128 torch from the (slow, in
#    China) PyPI mirror only to overwrite it — that multi-GB transfer is what
#    kept timing out the China build ("mirrors.aliyun.com: Read timed out").
#    torch/torchvision now come from the fast PyTorch index (TORCH_INDEX_URL).
# torchvision is pinned in lockstep: torch 2.7.1 pairs with torchvision 0.22.1
# (0.24.1's torchvision::nms C++ ext is ABI-incompatible with torch 2.7.1 and
# breaks `import torchvision`, cascading into Chronos failing to import).
# --no-deps keeps this layer to exactly these wheels; autogluon backfills its
# other (small) dependencies from PIP_INDEX_URL in the next layer.
# China (TORCH_FIND_LINKS set): resolve torch/torchvision/nvidia-*-cu11 from the
# Aliyun flat wheel dir (--find-links, locally hosted, no download-r2 timeout);
# triton resolves from PIP_INDEX_URL (Aliyun PyPI) since it's not in that dir.
# Commercial (TORCH_FIND_LINKS empty): use --index-url TORCH_INDEX_URL as before.
RUN set -eu; \
    PKGS="torch==2.7.1+cu118 torchvision==0.22.1+cu118 \
nvidia-cudnn-cu11==9.1.0.70 nvidia-cublas-cu11==11.11.3.6 \
nvidia-cuda-cupti-cu11==11.8.87 nvidia-cuda-nvrtc-cu11==11.8.89 \
nvidia-cuda-runtime-cu11==11.8.89 nvidia-cufft-cu11==10.9.0.58 \
nvidia-curand-cu11==10.3.0.86 nvidia-cusolver-cu11==11.4.1.48 \
nvidia-cusparse-cu11==11.7.5.86 nvidia-nccl-cu11==2.21.5 \
nvidia-nvtx-cu11==11.8.86 triton==3.3.1"; \
    if [ -n "${TORCH_FIND_LINKS}" ]; then \
      pip install --no-cache-dir --no-deps --find-links "${TORCH_FIND_LINKS}" ${PKGS}; \
    else \
      pip install --no-cache-dir --no-deps --index-url "${TORCH_INDEX_URL}" ${PKGS}; \
    fi

# Install autogluon (+ the rest) from PIP_INDEX_URL. torch is already satisfied
# by the cu118 build above, so this no longer re-downloads the huge cu128 torch;
# pip only fetches autogluon's remaining (small) dependencies.
COPY training_runtime/requirements.txt /tmp/training-runtime-requirements.txt
RUN pip install --no-cache-dir -r /tmp/training-runtime-requirements.txt

# Guard: fail the build loudly if the autogluon install silently upgraded torch
# off the cu118 build (e.g. a future autogluon that hard-pins a cu128 torch).
# Without this the image would build "successfully" but break at GPU runtime.
RUN python -c "import torch, torchvision; assert torch.__version__.startswith('2.7.1+cu118'), torch.__version__; assert torchvision.__version__.startswith('0.22.1+cu118'), torchvision.__version__; print('torch/torchvision cu118 OK:', torch.__version__, torchvision.__version__)"

# Pre-download the Chronos weights into the in-image HF cache. Uses HF_ENDPOINT
# from the build env so China builds pull from the mirror. This is the one and
# only network fetch of the weights; at runtime the model loads from this cache
# (huggingface_hub resolves the cache from HF_HOME automatically). CHRONOS_MODEL_ID
# is an ARG, exposed to RUN as an environment variable during the build.
RUN python -c "import os; from huggingface_hub import snapshot_download; snapshot_download(os.environ['CHRONOS_MODEL_ID'])"

COPY training_runtime /work/training_runtime

RUN chmod +x /work/training_runtime/entrypoint.sh

ENTRYPOINT ["/work/training_runtime/entrypoint.sh"]

# ---------------------------------------------------------------------------
# serve: SageMaker endpoint variant. Identical content, but runs as a non-root
# user. The inference server only READS the pre-baked HF cache and the model
# SageMaker extracts into /opt/ml/model; the only write path it needs is the
# workspace/tmp area, granted below. Training keeps using the root stage.
FROM training AS serve

RUN groupadd --gid 1000 sagemaker \
    && useradd --uid 1000 --gid sagemaker --create-home sagemaker \
    # AutoGluon/pandas may write small runtime caches; keep them in HOME.
    # /work is the WORKDIR — the serve process itself only reads from it, but
    # hand it to the user anyway so any incidental relative-path write works.
    && mkdir -p /opt/ml \
    && chown -R sagemaker:sagemaker /work /opt/ml

ENV HOME=/home/sagemaker \
    MPLCONFIGDIR=/home/sagemaker/.cache/matplotlib

USER sagemaker
