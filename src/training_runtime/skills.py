# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from training_runtime.contracts import RuntimeContract, split_s3_uri
from training_runtime.config import RuntimeConfig
from training_runtime.workspace import WorkspaceContext


@dataclass(frozen=True)
class EffectiveSkill:
    file_name: str
    source: str
    local_path: Path
    content: str


@dataclass(frozen=True)
class EffectiveSkillBundle:
    ordered_skills: tuple[EffectiveSkill, ...]

    @property
    def combined_system_prompt(self) -> str:
        return "\n\n".join(skill.content for skill in self.ordered_skills if skill.content)

    @property
    def sources(self) -> list[dict[str, str]]:
        return [
            {
                "file_name": skill.file_name,
                "source": skill.source,
                "local_path": str(skill.local_path),
            }
            for skill in self.ordered_skills
        ]


def _read_skill_file(path: Path, source: str) -> EffectiveSkill:
    content = path.read_text(encoding="utf-8")
    return EffectiveSkill(
        file_name=path.name,
        source=source,
        local_path=path,
        content=content,
    )


def _download_skill_overrides(
    *,
    contract: RuntimeContract,
    workspace: WorkspaceContext,
    s3_client: Any,
) -> dict[str, EffectiveSkill]:
    bucket, prefix = split_s3_uri(contract.skills_uri)
    response = s3_client.list_objects_v2(Bucket=bucket, Prefix=prefix)
    items = response.get("Contents", [])
    overrides: dict[str, EffectiveSkill] = {}
    for item in items:
        key = item.get("Key") or ""
        if not key or key.endswith("/"):
            continue
        file_name = Path(key).name
        target = workspace.skills_dir / file_name
        s3_client.download_file(bucket, key, str(target))
        overrides[file_name] = _read_skill_file(target, "s3")
    return overrides


def merge_skill_maps(
    defaults: dict[str, EffectiveSkill],
    overrides: dict[str, EffectiveSkill],
    required_order: tuple[str, ...],
) -> EffectiveSkillBundle:
    merged: list[EffectiveSkill] = []
    for file_name in required_order:
        merged.append(overrides.get(file_name, defaults[file_name]))
    return EffectiveSkillBundle(ordered_skills=tuple(merged))


def load_effective_skill_bundle(
    *,
    contract: RuntimeContract,
    config: RuntimeConfig,
    workspace: WorkspaceContext,
    s3_client: Any | None = None,
) -> EffectiveSkillBundle:
    baked_skills_dir = Path(config.baked_skills_dir)
    required_order = (
        config.general_skill_name,
        config.domain_skill_name,
    )
    defaults = {
        file_name: _read_skill_file(baked_skills_dir / file_name, "baked-in")
        for file_name in required_order
    }
    overrides: dict[str, EffectiveSkill] = {}
    if s3_client is not None:
        downloaded = _download_skill_overrides(
            contract=contract,
            workspace=workspace,
            s3_client=s3_client,
        )
        overrides = {name: skill for name, skill in downloaded.items() if name in defaults}
    return merge_skill_maps(defaults, overrides, required_order)
