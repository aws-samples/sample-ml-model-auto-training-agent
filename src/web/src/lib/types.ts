// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
// A task-type id. The set of valid values is configured in cdk.json
// ("taskTypes") and served by GET /task-types, so this is an open string
// rather than a fixed union.
export type TaskType = string;

export interface TaskTypeOption {
  id: string;
  label: string;
}

export type UserRole = "admin" | "user";

export interface AuthenticatedUser {
  username: string;
  role: UserRole;
  displayName: string;
}

export interface FrontendRuntimeConfiguration {
  // In China this is a same-origin path prefix (e.g. "/poc") fronted by
  // CloudFront; in commercial regions it is the full API Gateway base URL.
  apiBaseUrl: string;
  environmentLabel: string;
  submittedBy: string;
  // Auth mode is chosen at runtime from which fields are populated:
  //  - pocToken present  -> commercial regions: sent as a plain Authorization
  //    header to the Lambda TokenAuthorizer (no ICP constraint).
  //  - signHost + credentials present -> China: each request is SigV4
  //    query-signed (IAM auth) because a token/unauthenticated call is blocked
  //    at the ICP edge. PoC posture: these values are visible to anyone who
  //    loads the frontend.
  pocToken?: string;
  signRegion?: string;
  // The API Gateway origin domain to sign Host against (NOT the CloudFront
  // domain). CloudFront forwards to this origin with Host = this value.
  signHost?: string;
  accessKeyId?: string;
  secretAccessKey?: string;
  sessionToken?: string;
}

export type IntervalUnit = "minutes" | "hours" | "days";

// "auto": deploy to SageMaker automatically once training succeeds.
// "manual": operator triggers deployment from the task detail page.
export type DeploymentMode = "auto" | "manual";

export interface SubmissionDraft {
  taskName: string;
  taskType: TaskType;
  dataLocation: string;
  outputLocation: string;
  modelName: string;
  targetEndpoint: string;
  // Free-text note about the task. Optional, and NOT part of the training
  // agent prompt — the agent's flow relies only on the two skills.
  comment: string;
  imageTag: string;
  deploymentMode: DeploymentMode;
  // Recurring task: when enabled, the task is not run immediately — an
  // EventBridge schedule fires it first at scheduleStart, then every interval.
  isRecurring: boolean;
  scheduleStart: string; // datetime-local value, e.g. "2026-07-02T09:00"
  intervalValue: string; // kept as string for the input; validated as int
  intervalUnit: IntervalUnit;
}

export interface DraftValidationResult {
  canPreview: boolean;
  fieldErrors: Partial<Record<keyof SubmissionDraft, string>>;
  summaryErrors: string[];
}

export interface ConfirmedTaskSubmission {
  task_name: string;
  task_type: TaskType;
  comment: string;
  data_location: string;
  output_location: string;
  model_name: string;
  target_endpoint: string;
  submitted_by: string;
  image_tag?: string;
  deployment_mode?: DeploymentMode;
  confirmation_flag: true;
  // Recurring task fields (only sent when is_recurring is true).
  is_recurring?: boolean;
  schedule_start?: string;
  interval_value?: number;
  interval_unit?: IntervalUnit;
}

export interface TrainingPlanPreview {
  resolvedImageTag: string;
  submittedBy: string;
  payload: ConfirmedTaskSubmission;
}

export interface SubmissionResultViewModel {
  taskId: string;
  status: string;
  message: string;
}

export interface TaskExecution {
  execution_id: string;
  status: string;
  batch_job_id?: string;
  started_at?: string;
  finished_at?: string;
  error_message?: string;
}

export interface TaskSummaryViewModel {
  task_id: string;
  task_name: string;
  status: string;
  task_type: string;
  model_name: string;
  target_endpoint: string;
  submitted_by: string;
  resolved_image_tag: string;
  created_at: string;
  updated_at: string;
  deployment_mode?: DeploymentMode;
  is_periodical?: boolean;
  is_recurring?: boolean;
  schedule_expression?: string;
  schedule_start?: string;
  interval_value?: number;
  interval_unit?: string;
  executions?: TaskExecution[];
}

export interface TaskLogEntry {
  timestamp?: string;
  level?: string;
  event_type?: string;
  attempt_number?: number;
  message?: string;
  details?: unknown;
  [key: string]: unknown;
}

export interface TaskLogViewModel {
  task_id: string;
  status: string;
  logs: TaskLogEntry[];
}

export interface ModelSummaryViewModel {
  model_record_id: string;
  task_id: string;
  model_name: string;
  target_endpoint: string;
  resolved_image_tag: string;
  status: string;
  provider_status?: string | null;
  provider_status_detail?: unknown;
  failure_reason?: string | null;
  artifact_reference?: unknown;
  endpoint_arn?: string | null;
  endpoint_config_name?: string | null;
  sagemaker_model_name?: string | null;
  is_current_endpoint_target?: boolean | null;
  created_at: string;
  updated_at: string;
}

export interface ModelListPlaceholderState {
  title: string;
  description: string;
}

export type PrimaryMenuKey = "tasks" | "infer" | "models" | "data" | "images";

export interface InferSubmissionDraft {
  taskName: string;
  taskType: TaskType;
  dataLocation: string;
  outputLocation: string;
  modelName: string;
  targetEndpoint: string;
  // CSV column holding the historical target values; renamed to `target` at
  // inference time. Defaults to "target".
  targetColumn: string;
  description: string;
}

export interface ConfirmedInferSubmission {
  task_name: string;
  task_type: TaskType;
  data_location: string;
  output_location: string;
  model_name: string;
  target_endpoint: string;
  submitted_by: string;
  description: string;
  target_column: string;
  confirmation_flag: true;
}

export interface InferTaskSummaryViewModel {
  task_id: string;
  task_name: string;
  status: string;
  task_type: string;
  model_name: string;
  target_endpoint: string;
  data_location: string;
  output_location: string;
  submitted_by: string;
  description: string;
  target_column?: string;
  result_location?: string;
  prediction_count?: number;
  created_at: string;
  updated_at: string;
}

export interface NotificationMessage {
  message_id: string;
  content: string;
  task_id: string;
  task_name: string;
  submitted_by: string;
  read?: boolean;
  created_at: string;
}

// A file downloaded inline through the API as base64 (China cannot use
// presigned S3 URLs — the 未备案 S3 domain is blocked for browser access).
export interface DownloadContent {
  file_name: string;
  content_base64: string;
  content_type: string;
}

export interface ImageEntry {
  tag: string;
  size: number;
  status: string;
  build_id?: string;
  skills?: string;
  created_at: string;
  updated_at: string;
}

export interface ImageListing {
  images: ImageEntry[];
}

// One editable domain skill (one task type's skill file). A training-agent
// image bakes in every domain skill; the editor lets the user switch between
// them.
export interface DomainSkill {
  file_name: string;
  content: string;
}

// Default general skill text plus every domain skill, used to pre-fill the
// skill editors when orchestrating a new training-agent image.
export interface SkillTemplatesResponse {
  general_skill_name: string;
  general_skill: string;
  domain_skills: DomainSkill[];
}

export interface ImageBuildStartResponse {
  build_id: string;
  build_status: string;
  tag: string;
}

export interface DataFileEntry {
  key: string;
  name: string;
  size: number;
  last_modified?: string | null;
}

export interface DataFileListing {
  prefix: string;
  folders: string[];
  files: DataFileEntry[];
}

export interface UploadUrlResponse {
  upload_url: string;
  key: string;
  content_type: string;
  expires_in: number;
}

// The training run's evaluation_report.json. Its shape is agent-authored, so we
// keep the full object as `report` (rendered generically) plus task context.
export interface EvaluationReportViewModel {
  task_id: string;
  execution_id: string;
  status: string;
  report: Record<string, unknown> | null;
}

export type AsyncViewState = "idle" | "loading" | "success" | "error";

export type SubmissionFlowStage =
  | "bootstrap"
  | "welcome"
  | "task-type-selection"
  | "draft-edit"
  | "preview"
  | "submitting"
  | "submit-success"
  | "submit-failure";
