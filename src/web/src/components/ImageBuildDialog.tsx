// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import { useEffect, useState } from "react";

import { Modal } from "./Modal";
import type { ApiClient } from "../lib/api";
import type { DomainSkill } from "../lib/types";

interface ImageBuildDialogProps {
  open: boolean;
  apiClient: ApiClient;
  onClose: () => void;
  onStarted: () => void;
}

export function ImageBuildDialog({
  open,
  apiClient,
  onClose,
  onStarted,
}: ImageBuildDialogProps) {
  const [tag, setTag] = useState("");
  const [generalSkill, setGeneralSkill] = useState("");
  // Every domain skill is baked into a single image; the editor switches
  // between them via activeDomain (index into domainSkills).
  const [domainSkills, setDomainSkills] = useState<DomainSkill[]>([]);
  const [activeDomain, setActiveDomain] = useState(0);
  const [templatesLoaded, setTemplatesLoaded] = useState(false);
  const [loadingTemplates, setLoadingTemplates] = useState(false);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState("");
  const [error, setError] = useState("");

  // Load the default general skill plus every domain skill from the deployed
  // agent baseline so the editors are pre-filled. Reloads when the dialog opens.
  useEffect(() => {
    if (!open) {
      return;
    }
    let cancelled = false;
    setLoadingTemplates(true);
    setTemplatesLoaded(false);
    setError("");
    apiClient
      .getSkillTemplates()
      .then((templates) => {
        if (cancelled) return;
        setGeneralSkill(templates.general_skill);
        setDomainSkills(templates.domain_skills ?? []);
        setActiveDomain(0);
        setTemplatesLoaded(true);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "技能模板加载失败");
      })
      .finally(() => {
        if (cancelled) return;
        setLoadingTemplates(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, apiClient]);

  function handleClose() {
    if (busy) return;
    setTag("");
    setProgress("");
    setError("");
    setTemplatesLoaded(false);
    onClose();
  }

  function updateActiveDomainContent(content: string) {
    setDomainSkills((prev) =>
      prev.map((skill, index) =>
        index === activeDomain ? { ...skill, content } : skill,
      ),
    );
  }

  async function handleBuild() {
    if (busy) {
      return;
    }
    // Validate explicitly and surface WHY the build can't start, instead of a
    // silently-disabled button that looks like "nothing happens" on click.
    if (!tag.trim()) {
      setError("请填写镜像标签 (tag)");
      return;
    }
    if (loadingTemplates || !templatesLoaded) {
      setError("技能模板尚未加载完成，请稍候再试");
      return;
    }
    if (!generalSkill.trim()) {
      setError("通用 skill 内容不能为空");
      return;
    }
    if (domainSkills.length === 0) {
      setError("未加载到任何领域 skill，无法构建");
      return;
    }
    const emptyDomain = domainSkills.find((skill) => !skill.content.trim());
    if (emptyDomain) {
      setError(`领域 skill「${emptyDomain.file_name}」内容不能为空`);
      return;
    }
    try {
      setBusy(true);
      setError("");
      setProgress("编排 Agent 代码并启动构建...");
      const build = await apiClient.orchestrateImageBuild({
        tag: tag.trim(),
        generalSkill,
        domainSkills,
      });
      setProgress(`构建已启动：${build.build_id}`);
      setTag("");
      setTemplatesLoaded(false);
      onStarted();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "编排构建启动失败");
    } finally {
      setBusy(false);
    }
  }

  const activeSkill = domainSkills[activeDomain];

  return (
    <Modal
      open={open}
      title="新建训练Agent镜像"
      onClose={handleClose}
      testId="image-build-dialog"
    >
      <div className="field section">
        <span className="field-label">镜像标签 (tag)</span>
        <input
          type="text"
          placeholder="如 v2 或 experiment-a"
          value={tag}
          onChange={(event) => setTag(event.target.value)}
          data-testid="image-tag-input"
        />
        <span className="muted-text">
          构建产物将推送到 ECR 仓库的该标签；填 stable 会覆盖默认镜像。
        </span>
      </div>

      {loadingTemplates ? (
        <div className="muted-text section">技能模板加载中...</div>
      ) : null}

      <div className="field section">
        <span className="field-label">通用 skill（general-agent-spec）</span>
        <textarea
          rows={8}
          value={generalSkill}
          onChange={(event) => setGeneralSkill(event.target.value)}
          disabled={loadingTemplates}
          data-testid="image-general-skill-input"
        />
        <span className="muted-text">任务无关的通用 Agent 规范，可编辑。</span>
      </div>

      <div className="field section">
        <span className="field-label">领域 skill</span>
        {domainSkills.length > 0 ? (
          <select
            value={activeDomain}
            onChange={(event) => setActiveDomain(Number(event.target.value))}
            disabled={loadingTemplates}
            data-testid="image-domain-skill-select"
          >
            {domainSkills.map((skill, index) => (
              <option key={skill.file_name} value={index}>
                {skill.file_name}
              </option>
            ))}
          </select>
        ) : null}
        <textarea
          rows={12}
          value={activeSkill?.content ?? ""}
          onChange={(event) => updateActiveDomainContent(event.target.value)}
          disabled={loadingTemplates || !activeSkill}
          data-testid="image-domain-skill-input"
        />
        <span className="muted-text">
          可在上方切换不同任务类型的领域 skill 并分别编辑。镜像会烤入全部领域
          skill，后台把这些文本连同通用 skill 一起叠加到 Agent 代码基线上重新打包构建。
        </span>
      </div>

      {progress ? <div className="muted-text section">{progress}</div> : null}
      {error ? <div className="error-text section">{error}</div> : null}

      <div className="actions section">
        <button
          type="button"
          className="primary-button"
          disabled={busy}
          onClick={() => void handleBuild()}
          data-testid="image-build-confirm-button"
        >
          {busy ? "处理中..." : "开始构建"}
        </button>
        <button
          type="button"
          className="secondary-button"
          onClick={handleClose}
          disabled={busy}
        >
          取消
        </button>
      </div>
    </Modal>
  );
}
