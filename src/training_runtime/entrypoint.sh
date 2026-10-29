#!/usr/bin/env sh
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
set -eu

export PYTHONPATH="/work:${PYTHONPATH:-}"
# Run strands_tools (shell / python_repl / file_write) non-interactively: the
# Batch container has no TTY, so their default "Do you want to proceed? [y/*]"
# consent prompt would block forever. This makes tool execution autonomous.
export BYPASS_TOOL_CONSENT=true
export STRANDS_NON_INTERACTIVE=true

# Dual-mode image: SageMaker starts the container with the "serve" argument to
# run the inference server; AWS Batch runs it with no args for training.
if [ "${1:-}" = "serve" ]; then
    exec python -m training_runtime.serve
fi
exec python -m training_runtime.main
