// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
interface EvaluationReportPanelProps {
  report: Record<string, unknown> | null;
  isLoading: boolean;
  errorMessage: string;
}

// Top-level scalar fields we surface as a labelled summary grid (in this order).
// Anything else in the report is shown in the raw JSON section below.
const SUMMARY_FIELDS: ReadonlyArray<{ key: string; label: string }> = [
  { key: "metric_name", label: "评估指标" },
  { key: "metric_value", label: "指标值" },
  { key: "metric_unit", label: "单位" },
  { key: "target_threshold", label: "达标阈值" },
  { key: "is_achieved", label: "是否达标" },
  { key: "iterations", label: "迭代轮次" },
];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function formatScalar(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value === "number") return String(value);
  return String(value);
}

// Render a flat object of scalars (e.g. per-entity MAPE map) as a two-column
// table. Returns null when the value is not a flat scalar map.
function renderScalarMap(value: unknown): Array<[string, unknown]> | null {
  if (!isRecord(value)) return null;
  const entries = Object.entries(value);
  if (entries.length === 0) return null;
  const allScalar = entries.every(
    ([, v]) => v === null || ["string", "number", "boolean"].includes(typeof v),
  );
  return allScalar ? entries : null;
}

export function EvaluationReportPanel({
  report,
  isLoading,
  errorMessage,
}: EvaluationReportPanelProps) {
  if (isLoading) {
    return <div className="muted-text" data-testid="evaluation-loading">加载中...</div>;
  }
  if (errorMessage) {
    return <div className="error-text" data-testid="evaluation-error">{errorMessage}</div>;
  }
  if (!report) {
    return (
      <div className="empty-state" data-testid="evaluation-empty">
        暂无训练结果。仅当训练成功产出 evaluation_report.json 后可见。
      </div>
    );
  }

  const summaryRows = SUMMARY_FIELDS.filter((field) => field.key in report);
  // Known nested breakdown: per-entity/per-device results map.
  const perEntity =
    renderScalarMap(report.per_entity_results) ??
    renderScalarMap(report.per_device_results) ??
    renderScalarMap(report.per_item_results);

  return (
    <div className="evaluation-panel" data-testid="task-detail-evaluation">
      {summaryRows.length > 0 ? (
        <dl className="detail-info-grid" data-testid="evaluation-summary">
          {summaryRows.map((field) => (
            <div key={field.key} className="detail-info-row">
              <dt>{field.label}</dt>
              <dd>{formatScalar(report[field.key])}</dd>
            </div>
          ))}
        </dl>
      ) : null}

      {perEntity ? (
        <div className="section">
          <div className="section-heading panel-title">各实体结果</div>
          <table className="data-table" data-testid="evaluation-per-entity">
            <thead>
              <tr>
                <th>实体</th>
                <th>指标值</th>
              </tr>
            </thead>
            <tbody>
              {perEntity.map(([name, value]) => (
                <tr key={name}>
                  <td>{name}</td>
                  <td>{formatScalar(value)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      <details className="section" data-testid="evaluation-raw">
        <summary className="panel-title" style={{ cursor: "pointer" }}>
          完整报告 (JSON)
        </summary>
        <pre className="log-content">{JSON.stringify(report, null, 2)}</pre>
      </details>
    </div>
  );
}
