// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import { useMemo, useState } from "react";

import type { DomainSkill, ImageEntry } from "../lib/types";

interface ImageDetailViewProps {
  image: ImageEntry;
  onBack: () => void;
}

interface ParsedSkills {
  generalSkillName: string;
  generalSkill: string;
  domainSkills: DomainSkill[];
}

function formatSize(bytes: number): string {
  if (!bytes) return "—";
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

// The image row persists its skills as a JSON string:
//   { general_skill_name, general_skill, domain_skills: [{file_name, content}] }
// Parse defensively so a legacy/malformed row still renders the rest of the
// detail rather than crashing.
function parseSkills(raw: string | undefined): ParsedSkills | null {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    const domainSkills = Array.isArray(parsed.domain_skills)
      ? (parsed.domain_skills as unknown[]).flatMap((entry) => {
          if (
            entry &&
            typeof entry === "object" &&
            typeof (entry as DomainSkill).file_name === "string" &&
            typeof (entry as DomainSkill).content === "string"
          ) {
            return [entry as DomainSkill];
          }
          return [];
        })
      : [];
    return {
      generalSkillName:
        typeof parsed.general_skill_name === "string"
          ? parsed.general_skill_name
          : "general-agent-spec.md",
      generalSkill:
        typeof parsed.general_skill === "string" ? parsed.general_skill : "",
      domainSkills,
    };
  } catch {
    return null;
  }
}

const INFO_ROWS: ReadonlyArray<{ label: string; value: (i: ImageEntry) => string }> = [
  { label: "镜像标签 (tag)", value: (i) => i.tag || "—" },
  { label: "状态", value: (i) => i.status || "—" },
  { label: "大小", value: (i) => formatSize(i.size) },
  { label: "构建 ID", value: (i) => i.build_id || "—" },
  { label: "创建时间", value: (i) => i.created_at || "—" },
  { label: "更新时间", value: (i) => i.updated_at || "—" },
];

export function ImageDetailView({ image, onBack }: ImageDetailViewProps) {
  const skills = useMemo(() => parseSkills(image.skills), [image.skills]);
  // Which skill's content is shown: "general" or a domain skill index.
  const [activeSkill, setActiveSkill] = useState<string>("general");

  const activeContent = useMemo(() => {
    if (!skills) return "";
    if (activeSkill === "general") return skills.generalSkill;
    const index = Number(activeSkill);
    return skills.domainSkills[index]?.content ?? "";
  }, [skills, activeSkill]);

  return (
    <section className="detail-view" data-testid="image-detail-view">
      <div className="detail-header">
        <button
          type="button"
          className="secondary-button"
          onClick={onBack}
          data-testid="image-detail-back-button"
        >
          ← 返回镜像列表
        </button>
        <div>
          <div className="detail-title">{image.tag || "(未命名)"}</div>
          <div className="muted-text">训练Agent镜像</div>
        </div>
        <span className="status-badge">{image.status || "—"}</span>
      </div>

      <dl className="detail-info-grid" data-testid="image-detail-info">
        {INFO_ROWS.map((row) => (
          <div key={row.label} className="detail-info-row">
            <dt>{row.label}</dt>
            <dd>{row.value(image)}</dd>
          </div>
        ))}
      </dl>

      <div className="field section">
        <span className="field-label">烤入的 Skills</span>
        {skills ? (
          <>
            <select
              value={activeSkill}
              onChange={(event) => setActiveSkill(event.target.value)}
              data-testid="image-detail-skill-select"
            >
              <option value="general">
                通用 skill（{skills.generalSkillName}）
              </option>
              {skills.domainSkills.map((skill, index) => (
                <option key={skill.file_name} value={String(index)}>
                  领域 skill（{skill.file_name}）
                </option>
              ))}
            </select>
            <textarea
              rows={16}
              value={activeContent}
              readOnly
              data-testid="image-detail-skill-content"
            />
            <span className="muted-text">
              该镜像已烤入通用 skill 与全部领域 skill，可在上方切换查看各自内容（只读）。
            </span>
          </>
        ) : (
          <div className="muted-text" data-testid="image-detail-no-skills">
            无 skills 记录（该镜像可能构建于旧版本，未持久化 skill 内容）。
          </div>
        )}
      </div>
    </section>
  );
}
