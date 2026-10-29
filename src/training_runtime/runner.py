# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
from typing import Any, Protocol

import boto3

from training_runtime.config import RuntimeConfig
from training_runtime.contracts import RuntimeContract
from training_runtime.manifest import AttemptSummary, build_runtime_manifest, write_manifest
from training_runtime.outcomes import TerminalOutcome, determine_terminal_outcome
from training_runtime.providers import ProviderSelection, build_model, resolve_provider_selection
from training_runtime.publisher import OutputPublisher
from training_runtime.runtime_logging import JsonlRuntimeLog
from training_runtime.skills import EffectiveSkillBundle, load_effective_skill_bundle
from training_runtime.verification import VerificationResult, verify_attempt_output
from training_runtime.workspace import (
    WorkspaceContext,
    clear_directory,
    copy_directory_contents,
    materialize_s3_reference,
)


class AttemptExecutor(Protocol):
    def execute(
        self,
        *,
        attempt_number: int,
        retry_guidance: str | None,
        contract: RuntimeContract,
        config: RuntimeConfig,
        provider_selection: ProviderSelection,
        skill_bundle: EffectiveSkillBundle,
        workspace: WorkspaceContext,
        attempt_dir: Path,
        runtime_log: JsonlRuntimeLog,
    ) -> "AttemptExecutionResult":
        ...


@dataclass(frozen=True)
class AttemptExecutionResult:
    attempt_number: int
    output_dir: Path
    claimed_metric_value: float | None
    agent_summary: str


class StrandsAttemptExecutor:
    def __init__(self) -> None:
        self._metric_pattern_cache: dict[str, re.Pattern[str]] = {}

    def execute(
        self,
        *,
        attempt_number: int,
        retry_guidance: str | None,
        contract: RuntimeContract,
        config: RuntimeConfig,
        provider_selection: ProviderSelection,
        skill_bundle: EffectiveSkillBundle,
        workspace: WorkspaceContext,
        attempt_dir: Path,
        runtime_log: JsonlRuntimeLog,
    ) -> AttemptExecutionResult:
        # strands_tools read these at import/run time to decide whether to ask
        # for interactive consent before running shell/python/file-write tools.
        # The Batch container has no TTY, so force non-interactive execution to
        # avoid a "Do you want to proceed? [y/*]" prompt blocking the run.
        os.environ.setdefault("BYPASS_TOOL_CONSENT", "true")
        os.environ.setdefault("STRANDS_NON_INTERACTIVE", "true")
        from strands import Agent
        from strands_tools import file_read, file_write, python_repl, shell

        conversation_manager = _build_conversation_manager(config)

        prompt = _build_attempt_prompt(
            contract=contract,
            workspace=workspace,
            attempt_dir=attempt_dir,
            attempt_number=attempt_number,
            retry_guidance=retry_guidance,
        )
        runtime_log.write(
            "attempt-started",
            task_id=contract.task_id,
            attempt_number=attempt_number,
            provider_mode=provider_selection.provider_mode,
        )
        agent = Agent(
            model=build_model(provider_selection),
            system_prompt=skill_bundle.combined_system_prompt,
            tools=[shell, file_read, file_write, python_repl],
            callback_handler=None,
            conversation_manager=conversation_manager,
        )
        # Run the agent from the attempt directory so any relative path its
        # tools write (e.g. a skill's path="models/") lands inside the attempt
        # output that gets verified and published, not in the task root.
        previous_cwd = os.getcwd()
        os.chdir(attempt_dir)
        try:
            result = _invoke_agent_with_context_recovery(
                agent=agent,
                prompt=prompt,
                runtime_log=runtime_log,
                contract=contract,
                attempt_number=attempt_number,
            )
        finally:
            os.chdir(previous_cwd)
        summary = _extract_text(result.message)
        metric_pattern = self._metric_pattern_cache.setdefault(
            config.target_metric_name,
            re.compile(rf"{re.escape(config.target_metric_name)}[=:]\s*([0-9.]+)", re.IGNORECASE),
        )
        metric = _extract_claimed_metric(summary, pattern=metric_pattern)
        runtime_log.write(
            "attempt-finished",
            task_id=contract.task_id,
            attempt_number=attempt_number,
            claimed_metric_value=metric,
        )
        return AttemptExecutionResult(
            attempt_number=attempt_number,
            output_dir=attempt_dir,
            claimed_metric_value=metric,
            agent_summary=summary,
        )


def _build_conversation_manager(config: RuntimeConfig) -> Any:
    # Cap how much conversation history is sent back to the model each turn so a
    # long multi-tool training run cannot grow the prompt past the model context
    # window. The sliding window keeps the most recent N message pairs and drops
    # older ones; it also trims oversized tool results on context-overflow.
    from strands.agent.conversation_manager import SlidingWindowConversationManager

    window = max(4, config.conversation_window_messages)
    return SlidingWindowConversationManager(
        window_size=window,
        should_truncate_results=True,
    )


def _describe_exception(error: BaseException) -> str:
    """Render an exception together with its cause chain and types.

    Client SDKs (e.g. the OpenAI client) wrap the real transport failure in a
    generic ``APIConnectionError`` whose ``str()`` is just "Connection error.".
    The actual reason — DNS failure, no route, connection refused, TLS error —
    lives in ``__cause__``. Flattening the chain here makes the training log say
    WHICH kind of connection failure it was, so an operator can tell a DNS
    problem from a routing/firewall problem without re-running.
    """
    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        message = str(current).strip()
        label = type(current).__name__
        parts.append(f"{label}: {message}" if message else label)
        current = current.__cause__ or current.__context__
    return " <- ".join(parts)


def _is_context_overflow_error(error: Exception) -> bool:
    text = str(error).lower()
    return (
        "context length" in text
        or "input tokens" in text
        or "too many tokens" in text
        or "maximum context" in text
        or "context window" in text
    )


def _invoke_agent_with_context_recovery(
    *,
    agent: Any,
    prompt: str,
    runtime_log: JsonlRuntimeLog,
    contract: RuntimeContract,
    attempt_number: int,
    max_compactions: int = 3,
) -> Any:
    """Run the agent; if the model rejects the request because the prompt exceeds
    the context window, aggressively compact the conversation history and retry
    instead of letting the whole attempt crash."""
    try:
        return agent(prompt)
    except Exception as exc:  # noqa: BLE001 - we re-raise non-overflow errors
        if not _is_context_overflow_error(exc):
            raise

    follow_up = (
        "Continue the task. The previous context was too large and older "
        "messages were dropped; rely on files already written under the output "
        "directory and do not repeat completed work. "
        "Before you finish, `ls` the output directory and confirm every required "
        "prediction CSV has actually been written to disk; if any is missing, "
        "export it now — the task is NOT complete until the prediction CSVs and "
        "evaluation_report.json exist on disk, no matter what was done earlier."
    )
    for compaction in range(1, max_compactions + 1):
        runtime_log.write(
            "context-compacted",
            task_id=contract.task_id,
            attempt_number=attempt_number,
            level="WARN",
            compaction=compaction,
        )
        _compact_messages(agent)
        try:
            return agent(follow_up)
        except Exception as exc:  # noqa: BLE001
            if not _is_context_overflow_error(exc) or compaction == max_compactions:
                raise
    # Unreachable, but keeps type checkers happy.
    raise RuntimeError("context compaction failed to recover")


def _message_has_block(message: Any, key: str) -> bool:
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return False
    return any(isinstance(block, dict) and key in block for block in content)


def _compact_messages(agent: Any) -> None:
    """Drop the oldest part of the conversation to shrink the prompt.

    Bedrock requires the message list to start with a ``user`` message that is
    NOT a dangling ``toolResult`` (its matching ``toolUse`` would have been
    dropped). So after picking a rough cut point, advance it forward until the
    new first message is a clean user turn (text, no toolResult), keeping
    tool-use / tool-result pairs intact."""
    messages = getattr(agent, "messages", None)
    if not messages or len(messages) <= 2:
        return

    cut = len(messages) // 2
    # Advance the cut until messages[cut] is a safe conversation start: a user
    # message without a toolResult block (i.e. not an orphaned tool result).
    while cut < len(messages):
        candidate = messages[cut]
        is_user = isinstance(candidate, dict) and candidate.get("role") == "user"
        if is_user and not _message_has_block(candidate, "toolResult"):
            break
        cut += 1

    # If we couldn't find a clean boundary, keep only the final user message.
    if cut >= len(messages):
        for index in range(len(messages) - 1, -1, -1):
            msg = messages[index]
            if (
                isinstance(msg, dict)
                and msg.get("role") == "user"
                and not _message_has_block(msg, "toolResult")
            ):
                cut = index
                break
        else:
            return  # nothing safe to cut to; leave history untouched

    if cut > 0:
        del messages[:cut]


def _extract_text(message: Any) -> str:
    if not isinstance(message, dict):
        return str(message)
    parts: list[str] = []
    for block in message.get("content", []):
        if isinstance(block, dict) and "text" in block:
            parts.append(block["text"])
    return "".join(parts)


def _extract_claimed_metric(text: str, *, pattern: re.Pattern[str]) -> float | None:
    match = pattern.search(text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _build_attempt_prompt(
    *,
    contract: RuntimeContract,
    workspace: WorkspaceContext,
    attempt_dir: Path,
    attempt_number: int,
    retry_guidance: str | None,
) -> str:
    output_dir = attempt_dir / "output"
    base_prompt = (
        f"Task {contract.task_id} attempt {attempt_number}\n"
        f"Training data should be materialized under: {workspace.input_dir}\n"
        f"Effective skills are materialized under: {workspace.skills_dir}\n"
        f"Write all attempt outputs under: {output_dir}\n\n"
        "EXECUTION CONTRACT (must follow exactly):\n"
        f"1. Use the absolute output directory {output_dir} for every produced "
        "file. Do NOT use a relative path such as ./output or ./output_combined; "
        "always resolve outputs to that absolute directory.\n"
        "2. Run the training and prediction SYNCHRONOUSLY and to completion. Do "
        "not launch training in the background (no nohup, no trailing '&', no "
        "detached process). Block until training, prediction, and evaluation "
        "have fully finished. (The exact modeling approach — time-series rolling "
        "forecast, tabular regression, etc. — is defined by the provided skill.)\n"
        f"3. Before you end your reply, the prediction CSV file(s) (named "
        f"prediction*.csv) and evaluation_report.json MUST already exist on disk "
        f"under {output_dir}. Verify they exist (e.g. list the directory) and "
        "only then report completion. Follow the skill for how many prediction "
        "files to produce and their exact contents.\n"
        "4. Do not end your reply early with only a plan or a started-in-background "
        "message; the run is complete only when the prediction files are written.\n"
        f"5. Save the trained model under {output_dir}/models using that absolute "
        "path (for AutoGluon pass path=<that directory> to the predictor). A "
        "model saved anywhere else is not published and cannot be deployed, and "
        "the attempt fails verification.\n"
    )
    # NOTE: the task comment (formerly TRAINING_PROMPT) is intentionally NOT
    # injected here. The training agent's flow is driven solely by the two
    # materialized skills; the user's comment is a human-facing note only.
    if not retry_guidance:
        return base_prompt
    return (
        base_prompt
        + "\n"
        + "Independent verification feedback from the previous attempt:\n"
        + f"{retry_guidance}\n"
        + "You must address this verification feedback before the next attempt completes.\n"
    )


@dataclass(frozen=True)
class RuntimeExecutionResult:
    manifest: dict[str, Any]
    terminal_outcome: TerminalOutcome


def _build_attempt_failure_result(
    *,
    config: RuntimeConfig,
    reason: str,
) -> VerificationResult:
    return VerificationResult(
        achieved=False,
        metric_name=config.target_metric_name,
        metric_value=None,
        threshold=config.target_metric_threshold,
        threshold_direction=config.target_metric_direction,
        reason=reason,
        missing_artifacts=("attempt execution",),
        source="runtime-exception",
    )


def run_training_runtime(
    *,
    contract: RuntimeContract,
    config: RuntimeConfig,
    workspace: WorkspaceContext,
    executor: AttemptExecutor | None = None,
    s3_client: Any | None = None,
    secrets_client: Any | None = None,
) -> RuntimeExecutionResult:
    executor_impl = executor or StrandsAttemptExecutor()
    s3 = s3_client or boto3.client("s3", region_name=contract.aws_region)
    runtime_log = JsonlRuntimeLog(workspace.logs_dir / "events.jsonl")
    publisher = OutputPublisher(s3)
    runtime_log.write("runtime-started", task_id=contract.task_id, provider_mode=config.model_provider)
    materialized_inputs = materialize_s3_reference(
        s3_client=s3,
        source_uri=contract.data_uri,
        destination_dir=workspace.input_dir,
    )
    runtime_log.write(
        "inputs-materialized",
        task_id=contract.task_id,
        file_count=len(materialized_inputs),
    )

    provider_selection = resolve_provider_selection(config, secrets_client=secrets_client)
    runtime_log.write(
        "provider-resolved",
        task_id=contract.task_id,
        provider_mode=provider_selection.provider_mode,
        model_id=provider_selection.model_id,
    )
    skill_bundle = load_effective_skill_bundle(
        contract=contract,
        config=config,
        workspace=workspace,
        s3_client=s3,
    )
    runtime_log.write(
        "skills-resolved",
        task_id=contract.task_id,
        sources=skill_bundle.sources,
    )
    attempts: list[AttemptSummary] = []
    terminal_outcome: TerminalOutcome | None = None
    execution_error: str | None = None
    retry_guidance: str | None = None

    for attempt_number in range(1, config.max_attempts + 1):
        attempt_dir = workspace.attempt_dir(attempt_number)
        clear_directory(attempt_dir)
        try:
            result = executor_impl.execute(
                attempt_number=attempt_number,
                retry_guidance=retry_guidance,
                contract=contract,
                config=config,
                provider_selection=provider_selection,
                skill_bundle=skill_bundle,
                workspace=workspace,
                attempt_dir=attempt_dir,
                runtime_log=runtime_log,
            )
        except Exception as exc:  # pragma: no cover - defensive runtime path
            # Flatten the cause chain so a bare "Connection error." is qualified
            # by its real transport reason (DNS / no route / refused / TLS).
            execution_error = _describe_exception(exc)
            runtime_log.write(
                "attempt-exception",
                task_id=contract.task_id,
                attempt_number=attempt_number,
                level="ERROR",
                error=execution_error,
            )
            verification = _build_attempt_failure_result(
                config=config,
                reason=f"Attempt execution failed: {execution_error}",
            )
            attempts.append(
                AttemptSummary(
                    attempt_number=attempt_number,
                    output_dir=str(attempt_dir),
                    claimed_metric_value=None,
                    verification_result=verification,
                )
            )
            retry_guidance = verification.reason
            if attempt_number == config.max_attempts:
                break
            continue

        verification = verify_attempt_output(
            output_dir=result.output_dir,
            config=config,
            contract=contract,
            workspace=workspace,
            s3_client=s3,
            explicit_metric_value=result.claimed_metric_value,
        )
        attempts.append(
            AttemptSummary(
                attempt_number=attempt_number,
                output_dir=str(result.output_dir),
                claimed_metric_value=result.claimed_metric_value,
                verification_result=verification,
            )
        )
        runtime_log.write(
            "attempt-verified",
            task_id=contract.task_id,
            attempt_number=attempt_number,
            achieved=verification.achieved,
            metric_value=verification.metric_value,
            reason=verification.reason,
        )
        runtime_log.maybe_sync(
            bucket=contract.bucket,
            key=contract.logs_prefix.rstrip("/") + "/events.jsonl",
            s3_client=s3,
            minimum_interval_seconds=config.log_sync_seconds,
        )
        terminal_outcome = determine_terminal_outcome(
            verification_result=verification,
            execution_error=None,
        )
        retry_guidance = verification.reason
        if verification.achieved:
            execution_error = None
            break

    if terminal_outcome is None:
        terminal_outcome = determine_terminal_outcome(
            verification_result=attempts[-1].verification_result if attempts else None,
            execution_error=execution_error,
        )
    if attempts:
        copy_directory_contents(
            Path(attempts[-1].output_dir),
            workspace.output_dir,
            include=(
                None
                if terminal_outcome.achieved
                else lambda path: not path.name.startswith("prediction")
            ),
        )

    log_reference = runtime_log.sync(
        bucket=contract.bucket,
        key=contract.logs_prefix.rstrip("/") + "/events.jsonl",
        s3_client=s3,
    )
    published_artifacts = publisher.publish_results(
        workspace_output_dir=workspace.output_dir,
        contract=contract,
    )
    manifest_path = workspace.output_dir / "runtime_manifest.json"
    manifest_reference = {
        "bucket": contract.bucket,
        "key": f"{contract.output_prefix.rstrip('/')}/runtime_manifest.json",
        "local_path": str(manifest_path),
    }
    manifest = build_runtime_manifest(
        contract=contract,
        provider_selection=provider_selection,
        skill_bundle=skill_bundle,
        attempts=attempts,
        terminal_outcome=terminal_outcome,
        log_reference={"bucket": log_reference.bucket, "key": log_reference.key},
        published_artifacts=[*published_artifacts, manifest_reference],
    )
    manifest_path = write_manifest(workspace.output_dir, manifest)
    publisher.upload_file(manifest_path, contract.output_uri)
    manifest["manifest_path"] = str(manifest_path)
    return RuntimeExecutionResult(manifest=manifest, terminal_outcome=terminal_outcome)
