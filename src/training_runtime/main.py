# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
from __future__ import annotations

import sys

from training_runtime.config import ConfigError, load_runtime_config
from training_runtime.contracts import ContractError, load_runtime_contract
from training_runtime.runner import run_training_runtime
from training_runtime.workspace import prepare_workspace


def main() -> int:
    try:
        contract = load_runtime_contract()
        config = load_runtime_config()
    except (ContractError, ConfigError) as exc:
        print(f"Runtime bootstrap error: {exc}", file=sys.stderr)
        return 2

    workspace = prepare_workspace(config.workspace_root, contract)
    result = run_training_runtime(
        contract=contract,
        config=config,
        workspace=workspace,
    )
    return 0 if result.terminal_outcome.achieved else 1


if __name__ == "__main__":
    raise SystemExit(main())
