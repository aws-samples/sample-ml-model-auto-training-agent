// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import type { ReactNode } from "react";

import type { PrimaryMenuKey } from "../lib/types";

interface SidebarProps {
  active: PrimaryMenuKey;
  onSelect: (key: PrimaryMenuKey) => void;
  // The 训练Agent镜像 menu is admin-only.
  isAdmin: boolean;
}

const ICON_PROPS = {
  width: 18,
  height: 18,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.8,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  "aria-hidden": true,
};

// Task icon: a checklist / clipboard.
function TasksIcon() {
  return (
    <svg {...ICON_PROPS}>
      <path d="M9 4h6a1 1 0 0 1 1 1v1H8V5a1 1 0 0 1 1-1Z" />
      <path d="M8 6H6a1 1 0 0 0-1 1v12a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V7a1 1 0 0 0-1-1h-2" />
      <path d="m9 13 1.5 1.5L13 12" />
      <path d="M9 17h6" />
    </svg>
  );
}

// Inference icon: a lightning bolt (a fast prediction run).
function InferIcon() {
  return (
    <svg {...ICON_PROPS}>
      <path d="M13 2 4 14h7l-1 8 9-12h-7l1-8Z" />
    </svg>
  );
}

// Model icon: a 3D cube (a trained artifact).
function ModelsIcon() {
  return (
    <svg {...ICON_PROPS}>
      <path d="M12 3 4 7v10l8 4 8-4V7l-8-4Z" />
      <path d="m4 7 8 4 8-4" />
      <path d="M12 11v10" />
    </svg>
  );
}

// Training-data icon: a database stack.
function DataIcon() {
  return (
    <svg {...ICON_PROPS}>
      <ellipse cx="12" cy="5" rx="7" ry="3" />
      <path d="M5 5v6c0 1.66 3.13 3 7 3s7-1.34 7-3V5" />
      <path d="M5 11v6c0 1.66 3.13 3 7 3s7-1.34 7-3v-6" />
    </svg>
  );
}

// Base-image icon: stacked container layers.
function ImagesIcon() {
  return (
    <svg {...ICON_PROPS}>
      <path d="m12 3 8 4-8 4-8-4 8-4Z" />
      <path d="m4 12 8 4 8-4" />
      <path d="m4 17 8 4 8-4" />
    </svg>
  );
}

const MENU_ITEMS: ReadonlyArray<{
  key: PrimaryMenuKey;
  label: string;
  icon: ReactNode;
}> = [
  { key: "tasks", label: "训练任务", icon: <TasksIcon /> },
  { key: "infer", label: "推理任务", icon: <InferIcon /> },
  { key: "models", label: "模型", icon: <ModelsIcon /> },
  { key: "data", label: "数据", icon: <DataIcon /> },
  { key: "images", label: "训练Agent镜像", icon: <ImagesIcon /> },
];

export function Sidebar({ active, onSelect, isAdmin }: SidebarProps) {
  // Only admins see the training-agent image menu.
  const visibleItems = MENU_ITEMS.filter(
    (item) => item.key !== "images" || isAdmin,
  );
  return (
    <nav className="sidebar" data-testid="sidebar" aria-label="主导航">
      <ul className="sidebar-menu">
        {visibleItems.map((item) => (
          <li key={item.key}>
            <button
              type="button"
              className={`sidebar-menu-item${active === item.key ? " is-active" : ""}`}
              aria-current={active === item.key ? "page" : undefined}
              onClick={() => onSelect(item.key)}
              data-testid={`sidebar-menu-${item.key}`}
            >
              <span className="sidebar-menu-icon" aria-hidden="true">
                {item.icon}
              </span>
              {item.label}
            </button>
          </li>
        ))}
      </ul>
    </nav>
  );
}
