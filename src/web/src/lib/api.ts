// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import { buildTokenHeaders, presignApiUrl } from "./auth";
import { usesTokenAuth } from "./runtimeConfig";
import type {
  ConfirmedInferSubmission,
  ConfirmedTaskSubmission,
  DataFileListing,
  DomainSkill,
  DownloadContent,
  EvaluationReportViewModel,
  FrontendRuntimeConfiguration,
  ImageBuildStartResponse,
  ImageListing,
  NotificationMessage,
  SkillTemplatesResponse,
  TaskTypeOption,
  InferTaskSummaryViewModel,
  ModelSummaryViewModel,
  TaskLogEntry,
  TaskLogViewModel,
  TaskSummaryViewModel,
  UploadUrlResponse,
} from "./types";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

async function parseResponseBody(response: Response): Promise<unknown> {
  const raw = await response.text();
  if (!raw) {
    return {};
  }
  try {
    return JSON.parse(raw) as unknown;
  } catch {
    return { message: raw };
  }
}

function getPayloadMessage(payload: unknown, fallback: string): string {
  if (isRecord(payload) && typeof payload.message === "string" && payload.message.trim()) {
    return payload.message;
  }
  return fallback;
}

function getPayloadErrorCode(payload: unknown): string | undefined {
  if (isRecord(payload) && typeof payload.error_code === "string" && payload.error_code.trim()) {
    return payload.error_code;
  }
  return undefined;
}

function normalizeLogEntry(entry: unknown): TaskLogEntry {
  if (isRecord(entry)) {
    return entry;
  }
  if (typeof entry === "string") {
    return { message: entry };
  }
  return { message: String(entry) };
}

export class ApiClientError extends Error {
  readonly code?: string;
  readonly status: number;

  constructor(message: string, options: { code?: string; status: number }) {
    super(options.code ? `[${options.code}] ${message}` : message);
    this.name = "ApiClientError";
    this.code = options.code;
    this.status = options.status;
  }
}

export class ApiClient {
  constructor(private readonly config: FrontendRuntimeConfiguration) {}

  private buildUrl(path: string): string {
    return `${this.config.apiBaseUrl.replace(/\/$/, "")}${path}`;
  }

  // Resolve a same-origin (possibly relative) API path to the absolute URL the
  // browser fetches, so it can be fed to the SigV4 signer (URL needs an origin).
  private absoluteUrl(path: string): string {
    return new URL(this.buildUrl(path), window.location.origin).toString();
  }

  // Perform an authenticated fetch, adapting to the runtime auth mode:
  //  - Commercial regions (pocToken present): send the token as a plain
  //    Authorization header to the Lambda TokenAuthorizer.
  //  - China (SigV4): presign the URL with query-string SigV4 — all signing
  //    material is in the query string (only `host` is signed), so no auth
  //    headers are sent.
  private async signedFetch(
    method: string,
    path: string,
    body?: string,
  ): Promise<Response> {
    if (usesTokenAuth(this.config)) {
      const headers = buildTokenHeaders(this.config, body !== undefined);
      return fetch(this.buildUrl(path), { method, headers, body });
    }
    const signedUrl = await presignApiUrl(this.config, {
      method,
      absoluteUrl: this.absoluteUrl(path),
      body,
    });
    // China SigV4 mode calls execute-api CROSS-ORIGIN. China public
    // execute-api returns 403 for any unsigned request — including the
    // anonymous CORS *preflight* OPTIONS the browser sends for "non-simple"
    // requests. A body with Content-Type: application/json would trigger that
    // preflight and get 403-blocked. So we send the JSON body as text/plain,
    // one of the CORS "simple request" content types, which skips the
    // preflight entirely; the signed POST/DELETE then goes straight through.
    // The backend parses the body with json.loads regardless of Content-Type,
    // so this is transparent server-side. (GET has no body and is already a
    // simple request.)
    const headers: Record<string, string> =
      body !== undefined
        ? { "Content-Type": "text/plain;charset=UTF-8" }
        : {};
    return fetch(signedUrl, { method, headers, body });
  }

  async submitTask(payload: ConfirmedTaskSubmission): Promise<{
    task_id: string;
    status: string;
    message: string;
  }> {
    const response = await this.signedFetch(
      "POST",
      "/tasks",
      JSON.stringify(payload),
    );
    const data = await parseResponseBody(response);
    if (!response.ok) {
      throw new ApiClientError(getPayloadMessage(data, "任务提交失败"), {
        code: getPayloadErrorCode(data),
        status: response.status,
      });
    }
    return {
      task_id:
        isRecord(data) && typeof data.task_id === "string" ? data.task_id : "",
      status: isRecord(data) && typeof data.status === "string" ? data.status : "",
      message:
        isRecord(data) && typeof data.message === "string" ? data.message : "",
    };
  }

  async getTasks(): Promise<TaskSummaryViewModel[]> {
    const response = await this.signedFetch("GET", "/tasks");
    const data = await parseResponseBody(response);
    if (!response.ok) {
      throw new ApiClientError(getPayloadMessage(data, "任务列表加载失败"), {
        code: getPayloadErrorCode(data),
        status: response.status,
      });
    }
    return isRecord(data) && Array.isArray(data.tasks)
      ? (data.tasks as TaskSummaryViewModel[])
      : [];
  }

  async cancelTask(taskId: string): Promise<{ task_id: string; status: string; message: string }> {
    const response = await this.signedFetch(
      "POST",
      "/tasks/cancel",
      JSON.stringify({ task_id: taskId }),
    );
    const data = await parseResponseBody(response);
    if (!response.ok) {
      throw new ApiClientError(getPayloadMessage(data, "取消任务失败"), {
        code: getPayloadErrorCode(data),
        status: response.status,
      });
    }
    return {
      task_id: isRecord(data) && typeof data.task_id === "string" ? data.task_id : taskId,
      status: isRecord(data) && typeof data.status === "string" ? data.status : "",
      message: isRecord(data) && typeof data.message === "string" ? data.message : "",
    };
  }

  async deployTask(taskId: string): Promise<{ task_id: string; status: string; message: string }> {
    const response = await this.signedFetch(
      "POST",
      "/tasks/deploy",
      JSON.stringify({ task_id: taskId }),
    );
    const data = await parseResponseBody(response);
    if (!response.ok) {
      throw new ApiClientError(getPayloadMessage(data, "部署失败"), {
        code: getPayloadErrorCode(data),
        status: response.status,
      });
    }
    return {
      task_id: isRecord(data) && typeof data.task_id === "string" ? data.task_id : taskId,
      status: isRecord(data) && typeof data.status === "string" ? data.status : "",
      message: isRecord(data) && typeof data.message === "string" ? data.message : "",
    };
  }

  async getModels(): Promise<ModelSummaryViewModel[]> {
    const response = await this.signedFetch("GET", "/models");
    const data = await parseResponseBody(response);
    if (!response.ok) {
      throw new ApiClientError(getPayloadMessage(data, "模型列表加载失败"), {
        code: getPayloadErrorCode(data),
        status: response.status,
      });
    }
    return isRecord(data) && Array.isArray(data.models)
      ? (data.models as ModelSummaryViewModel[])
      : [];
  }

  async login(
    username: string,
    password: string,
  ): Promise<{ username: string; role: string; display_name: string }> {
    const response = await this.signedFetch(
      "POST",
      "/login",
      JSON.stringify({ username, password }),
    );
    const data = await parseResponseBody(response);
    if (!response.ok) {
      throw new ApiClientError(getPayloadMessage(data, "登录失败"), {
        code: getPayloadErrorCode(data),
        status: response.status,
      });
    }
    return {
      username: isRecord(data) && typeof data.username === "string" ? data.username : username,
      role: isRecord(data) && typeof data.role === "string" ? data.role : "user",
      display_name:
        isRecord(data) && typeof data.display_name === "string"
          ? data.display_name
          : username,
    };
  }

  async getMessages(recipient: string): Promise<NotificationMessage[]> {
    const response = await this.signedFetch(
      "GET",
      `/messages?recipient=${encodeURIComponent(recipient)}`,
    );
    const data = await parseResponseBody(response);
    if (!response.ok) {
      throw new ApiClientError(getPayloadMessage(data, "消息加载失败"), {
        code: getPayloadErrorCode(data),
        status: response.status,
      });
    }
    return isRecord(data) && Array.isArray(data.messages)
      ? (data.messages as NotificationMessage[])
      : [];
  }

  async markMessageRead(messageId: string): Promise<void> {
    const response = await this.signedFetch(
      "POST",
      "/messages",
      JSON.stringify({ action: "mark-read", message_id: messageId }),
    );
    if (!response.ok) {
      const data = await parseResponseBody(response);
      throw new ApiClientError(getPayloadMessage(data, "标记已读失败"), {
        code: getPayloadErrorCode(data),
        status: response.status,
      });
    }
  }

  async getInferTasks(): Promise<InferTaskSummaryViewModel[]> {
    const response = await this.signedFetch("GET", "/infer-tasks");
    const data = await parseResponseBody(response);
    if (!response.ok) {
      throw new ApiClientError(getPayloadMessage(data, "推理任务列表加载失败"), {
        code: getPayloadErrorCode(data),
        status: response.status,
      });
    }
    return isRecord(data) && Array.isArray(data.tasks)
      ? (data.tasks as InferTaskSummaryViewModel[])
      : [];
  }

  async getInferResultDownload(taskId: string): Promise<DownloadContent> {
    const response = await this.signedFetch(
      "GET",
      `/infer-tasks?task_id=${encodeURIComponent(taskId)}&download=1`,
    );
    const data = await parseResponseBody(response);
    if (!response.ok) {
      throw new ApiClientError(getPayloadMessage(data, "预测结果下载失败"), {
        code: getPayloadErrorCode(data),
        status: response.status,
      });
    }
    const record = isRecord(data) ? data : {};
    return {
      file_name:
        typeof record.file_name === "string" ? record.file_name : "predictions.json",
      content_base64:
        typeof record.content_base64 === "string" ? record.content_base64 : "",
      content_type:
        typeof record.content_type === "string"
          ? record.content_type
          : "application/octet-stream",
    };
  }

  async submitInferTask(payload: ConfirmedInferSubmission): Promise<{
    task_id: string;
    status: string;
    message: string;
  }> {
    const response = await this.signedFetch(
      "POST",
      "/infer-tasks",
      JSON.stringify(payload),
    );
    const data = await parseResponseBody(response);
    if (!response.ok) {
      throw new ApiClientError(getPayloadMessage(data, "推理任务提交失败"), {
        code: getPayloadErrorCode(data),
        status: response.status,
      });
    }
    return {
      task_id:
        isRecord(data) && typeof data.task_id === "string" ? data.task_id : "",
      status: isRecord(data) && typeof data.status === "string" ? data.status : "",
      message:
        isRecord(data) && typeof data.message === "string" ? data.message : "",
    };
  }

  async listDataFiles(prefix?: string): Promise<DataFileListing> {
    const query = prefix ? `?prefix=${encodeURIComponent(prefix)}` : "";
    const response = await this.signedFetch("GET", `/data-files${query}`);
    const data = await parseResponseBody(response);
    if (!response.ok) {
      throw new ApiClientError(getPayloadMessage(data, "数据文件列表加载失败"), {
        code: getPayloadErrorCode(data),
        status: response.status,
      });
    }
    const record = isRecord(data) ? data : {};
    return {
      prefix: typeof record.prefix === "string" ? record.prefix : "data/",
      folders: Array.isArray(record.folders) ? (record.folders as string[]) : [],
      files: Array.isArray(record.files) ? (record.files as DataFileListing["files"]) : [],
    };
  }

  async createUploadUrl(params: {
    prefix?: string;
    subdirectory?: string;
    fileName: string;
    contentType: string;
  }): Promise<UploadUrlResponse> {
    const response = await this.signedFetch(
      "POST",
      "/data-files",
      JSON.stringify({
        prefix: params.prefix,
        subdirectory: params.subdirectory,
        file_name: params.fileName,
        content_type: params.contentType,
      }),
    );
    const data = await parseResponseBody(response);
    if (!response.ok) {
      throw new ApiClientError(getPayloadMessage(data, "上传地址获取失败"), {
        code: getPayloadErrorCode(data),
        status: response.status,
      });
    }
    return data as UploadUrlResponse;
  }

  async uploadToPresignedUrl(
    uploadUrl: string,
    file: File,
    contentType: string,
  ): Promise<void> {
    const response = await fetch(uploadUrl, {
      method: "PUT",
      headers: { "Content-Type": contentType },
      body: file,
    });
    if (!response.ok) {
      throw new ApiClientError("文件上传到存储失败", { status: response.status });
    }
  }

  async getFileDownload(key: string): Promise<DownloadContent> {
    const query = `?action=download&key=${encodeURIComponent(key)}`;
    const response = await this.signedFetch("GET", `/data-files${query}`);
    const data = await parseResponseBody(response);
    if (!response.ok) {
      throw new ApiClientError(getPayloadMessage(data, "文件下载失败"), {
        code: getPayloadErrorCode(data),
        status: response.status,
      });
    }
    const record = isRecord(data) ? data : {};
    return {
      file_name:
        typeof record.file_name === "string"
          ? record.file_name
          : key.split("/").pop() || "download",
      content_base64:
        typeof record.content_base64 === "string" ? record.content_base64 : "",
      content_type:
        typeof record.content_type === "string"
          ? record.content_type
          : "application/octet-stream",
    };
  }

  async deleteDataFile(key: string): Promise<void> {
    const response = await this.signedFetch(
      "DELETE",
      "/data-files",
      JSON.stringify({ key }),
    );
    const data = await parseResponseBody(response);
    if (!response.ok) {
      throw new ApiClientError(getPayloadMessage(data, "文件删除失败"), {
        code: getPayloadErrorCode(data),
        status: response.status,
      });
    }
  }

  async listImages(): Promise<ImageListing> {
    const response = await this.signedFetch("GET", "/images");
    const data = await parseResponseBody(response);
    if (!response.ok) {
      throw new ApiClientError(getPayloadMessage(data, "镜像列表加载失败"), {
        code: getPayloadErrorCode(data),
        status: response.status,
      });
    }
    const record = isRecord(data) ? data : {};
    return {
      images: Array.isArray(record.images) ? (record.images as ImageListing["images"]) : [],
    };
  }

  async getTaskTypes(): Promise<TaskTypeOption[]> {
    const response = await this.signedFetch("GET", "/task-types");
    const data = await parseResponseBody(response);
    if (!response.ok) {
      throw new ApiClientError(getPayloadMessage(data, "任务类型加载失败"), {
        code: getPayloadErrorCode(data),
        status: response.status,
      });
    }
    const list = isRecord(data) && Array.isArray(data.task_types) ? data.task_types : [];
    return list
      .filter(
        (item): item is TaskTypeOption =>
          isRecord(item) &&
          typeof item.id === "string" &&
          typeof item.label === "string",
      )
      .map((item) => ({ id: item.id, label: item.label }));
  }

  async getSkillTemplates(): Promise<SkillTemplatesResponse> {
    const response = await this.signedFetch(
      "POST",
      "/images",
      JSON.stringify({ action: "skill-templates" }),
    );
    const data = await parseResponseBody(response);
    if (!response.ok) {
      throw new ApiClientError(getPayloadMessage(data, "技能模板加载失败"), {
        code: getPayloadErrorCode(data),
        status: response.status,
      });
    }
    return data as SkillTemplatesResponse;
  }

  async orchestrateImageBuild(params: {
    tag: string;
    generalSkill: string;
    domainSkills: DomainSkill[];
  }): Promise<ImageBuildStartResponse> {
    const response = await this.signedFetch(
      "POST",
      "/images",
      JSON.stringify({
        action: "orchestrate",
        tag: params.tag,
        general_skill: params.generalSkill,
        domain_skills: params.domainSkills,
      }),
    );
    const data = await parseResponseBody(response);
    if (!response.ok) {
      throw new ApiClientError(getPayloadMessage(data, "编排构建启动失败"), {
        code: getPayloadErrorCode(data),
        status: response.status,
      });
    }
    return data as ImageBuildStartResponse;
  }

  async getLogs(taskId: string, executionId?: string): Promise<TaskLogViewModel> {
    const executionParam = executionId
      ? `&execution_id=${encodeURIComponent(executionId)}`
      : "";
    const response = await this.signedFetch(
      "GET",
      `/logs?task_id=${encodeURIComponent(taskId)}${executionParam}`,
    );
    const data = await parseResponseBody(response);
    if (!response.ok) {
      throw new ApiClientError(getPayloadMessage(data, "日志加载失败"), {
        code: getPayloadErrorCode(data),
        status: response.status,
      });
    }
    return {
      task_id:
        isRecord(data) && typeof data.task_id === "string" ? data.task_id : taskId,
      status: isRecord(data) && typeof data.status === "string" ? data.status : "UNKNOWN",
      logs:
        isRecord(data) && Array.isArray(data.logs)
          ? data.logs.map((entry) => normalizeLogEntry(entry))
          : [],
    };
  }

  async getEvaluation(
    taskId: string,
    executionId?: string,
  ): Promise<EvaluationReportViewModel> {
    const executionParam = executionId
      ? `&execution_id=${encodeURIComponent(executionId)}`
      : "";
    const response = await this.signedFetch(
      "GET",
      `/tasks/evaluation?task_id=${encodeURIComponent(taskId)}${executionParam}`,
    );
    const data = await parseResponseBody(response);
    if (!response.ok) {
      throw new ApiClientError(getPayloadMessage(data, "训练结果加载失败"), {
        code: getPayloadErrorCode(data),
        status: response.status,
      });
    }
    const record = isRecord(data) ? data : {};
    return {
      task_id: typeof record.task_id === "string" ? record.task_id : taskId,
      execution_id:
        typeof record.execution_id === "string" ? record.execution_id : "",
      status: typeof record.status === "string" ? record.status : "UNKNOWN",
      report: isRecord(record.report)
        ? (record.report as Record<string, unknown>)
        : null,
    };
  }
}
