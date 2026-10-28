// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import { useEffect, useMemo, useState } from "react";

import { DataFilePanel } from "./components/DataFilePanel";
import { DataUploadDialog } from "./components/DataUploadDialog";
import { HeaderBar } from "./components/HeaderBar";
import { ImageBuildDialog } from "./components/ImageBuildDialog";
import { ImageDetailView } from "./components/ImageDetailView";
import { ImageListPanel } from "./components/ImageListPanel";
import { InferTaskDetailView } from "./components/InferTaskDetailView";
import { InferTaskListPanel } from "./components/InferTaskListPanel";
import { InferTaskWizard } from "./components/InferTaskWizard";
import { LoginPage } from "./components/LoginPage";
import { Modal } from "./components/Modal";
import { ModelDetailView } from "./components/ModelDetailView";
import { ModelListPanel } from "./components/ModelListPanel";
import { Sidebar } from "./components/Sidebar";
import { TaskDetailView } from "./components/TaskDetailView";
import { TaskListPanel } from "./components/TaskListPanel";
import { TaskWizard } from "./components/TaskWizard";
import { ApiClient } from "./lib/api";
import { downloadBase64 } from "./lib/download";
import {
  loadRuntimeConfiguration,
  validateRuntimeConfiguration,
} from "./lib/runtimeConfig";
import type {
  AuthenticatedUser,
  ConfirmedInferSubmission,
  ConfirmedTaskSubmission,
  DataFileListing,
  EvaluationReportViewModel,
  FrontendRuntimeConfiguration,
  ImageListing,
  InferTaskSummaryViewModel,
  ModelSummaryViewModel,
  PrimaryMenuKey,
  SubmissionResultViewModel,
  TaskLogViewModel,
  TaskSummaryViewModel,
} from "./lib/types";

const AUTH_STORAGE_KEY = "demo-auth-user";

function loadStoredUser(): AuthenticatedUser | null {
  try {
    const raw = sessionStorage.getItem(AUTH_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as AuthenticatedUser;
    if (parsed && typeof parsed.username === "string" && parsed.role) {
      return parsed;
    }
  } catch {
    return null;
  }
  return null;
}

export default function App() {
  const [config, setConfig] = useState<FrontendRuntimeConfiguration | null>(null);
  const [configErrors, setConfigErrors] = useState<string[]>([]);
  const [user, setUser] = useState<AuthenticatedUser | null>(loadStoredUser);
  const [menu, setMenu] = useState<PrimaryMenuKey>("tasks");
  const [wizardOpen, setWizardOpen] = useState(false);

  const [tasks, setTasks] = useState<TaskSummaryViewModel[]>([]);
  const [tasksLoading, setTasksLoading] = useState(false);
  const [taskListError, setTaskListError] = useState("");
  const [selectedTaskId, setSelectedTaskId] = useState("");
  const [taskLogs, setTaskLogs] = useState<TaskLogViewModel | null>(null);
  const [logsLoading, setLogsLoading] = useState(false);
  const [taskLogError, setTaskLogError] = useState("");
  const [cancelling, setCancelling] = useState(false);
  const [deploying, setDeploying] = useState(false);
  const [evaluation, setEvaluation] = useState<EvaluationReportViewModel | null>(
    null,
  );
  const [evaluationLoading, setEvaluationLoading] = useState(false);
  const [evaluationError, setEvaluationError] = useState("");

  const [inferTasks, setInferTasks] = useState<InferTaskSummaryViewModel[]>([]);
  const [inferTasksLoading, setInferTasksLoading] = useState(false);
  const [inferTaskListError, setInferTaskListError] = useState("");
  const [selectedInferTaskId, setSelectedInferTaskId] = useState("");
  const [inferWizardOpen, setInferWizardOpen] = useState(false);

  const [models, setModels] = useState<ModelSummaryViewModel[]>([]);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [modelListError, setModelListError] = useState("");
  const [selectedModelId, setSelectedModelId] = useState("");

  const [dataListing, setDataListing] = useState<DataFileListing | null>(null);
  const [dataPrefix, setDataPrefix] = useState("data/");
  const [dataLoading, setDataLoading] = useState(false);
  const [dataError, setDataError] = useState("");
  const [uploadOpen, setUploadOpen] = useState(false);
  const [deletingKey, setDeletingKey] = useState("");

  const [imageListing, setImageListing] = useState<ImageListing | null>(null);
  const [imagesLoading, setImagesLoading] = useState(false);
  const [imageError, setImageError] = useState("");
  const [imageBuildOpen, setImageBuildOpen] = useState(false);
  const [selectedImageTag, setSelectedImageTag] = useState("");

  useEffect(() => {
    const loadedConfig = loadRuntimeConfiguration();
    setConfig(loadedConfig);
    setConfigErrors(validateRuntimeConfiguration(loadedConfig));
  }, []);

  // Bind submissions and message lookups to the logged-in user: the task
  // ``submitted_by`` becomes the username, which is also the notification
  // message recipient.
  const effectiveConfig = useMemo(
    () =>
      config && user
        ? { ...config, submittedBy: user.username }
        : config,
    [config, user],
  );

  const apiClient = useMemo(
    () => (effectiveConfig ? new ApiClient(effectiveConfig) : null),
    [effectiveConfig],
  );

  function handleLoggedIn(loggedIn: AuthenticatedUser) {
    setUser(loggedIn);
    try {
      sessionStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(loggedIn));
    } catch {
      /* sessionStorage unavailable — session is kept in memory only */
    }
  }

  function handleLogout() {
    setUser(null);
    try {
      sessionStorage.removeItem(AUTH_STORAGE_KEY);
    } catch {
      /* ignore */
    }
    setMenu("tasks");
  }

  const selectedTask = useMemo(
    () => tasks.find((task) => task.task_id === selectedTaskId) ?? null,
    [tasks, selectedTaskId],
  );
  const selectedModel = useMemo(
    () => models.find((model) => model.model_record_id === selectedModelId) ?? null,
    [models, selectedModelId],
  );
  const selectedInferTask = useMemo(
    () => inferTasks.find((task) => task.task_id === selectedInferTaskId) ?? null,
    [inferTasks, selectedInferTaskId],
  );
  const selectedImage = useMemo(
    () =>
      imageListing?.images.find((image) => image.tag === selectedImageTag) ??
      null,
    [imageListing, selectedImageTag],
  );

  async function refreshTasks() {
    if (!apiClient) {
      return;
    }
    try {
      setTasksLoading(true);
      setTaskListError("");
      setTasks(await apiClient.getTasks());
    } catch (error) {
      setTaskListError(error instanceof Error ? error.message : "任务列表加载失败");
    } finally {
      setTasksLoading(false);
    }
  }

  async function refreshModels() {
    if (!apiClient) {
      return;
    }
    try {
      setModelsLoading(true);
      setModelListError("");
      setModels(await apiClient.getModels());
    } catch (error) {
      setModelListError(error instanceof Error ? error.message : "模型列表加载失败");
    } finally {
      setModelsLoading(false);
    }
  }

  async function refreshInferTasks() {
    if (!apiClient) {
      return;
    }
    try {
      setInferTasksLoading(true);
      setInferTaskListError("");
      setInferTasks(await apiClient.getInferTasks());
    } catch (error) {
      setInferTaskListError(
        error instanceof Error ? error.message : "推理任务列表加载失败",
      );
    } finally {
      setInferTasksLoading(false);
    }
  }

  async function submitInferTask(
    payload: ConfirmedInferSubmission,
  ): Promise<SubmissionResultViewModel> {
    if (!apiClient) {
      throw new Error("运行时配置缺失，无法提交");
    }
    const response = await apiClient.submitInferTask(payload);
    await refreshInferTasks();
    return {
      taskId: response.task_id,
      status: response.status,
      message: response.message,
    };
  }

  async function refreshData(prefix: string) {
    if (!apiClient) {
      return;
    }
    try {
      setDataLoading(true);
      setDataError("");
      setDataPrefix(prefix);
      setDataListing(await apiClient.listDataFiles(prefix));
    } catch (error) {
      setDataError(error instanceof Error ? error.message : "数据文件加载失败");
    } finally {
      setDataLoading(false);
    }
  }

  async function downloadFile(key: string, name: string) {
    if (!apiClient) {
      return;
    }
    try {
      setDataError("");
      // China blocks browser access to presigned S3 URLs, so the file comes
      // back inline as base64 through the signed API and is saved locally.
      const { content_base64, file_name, content_type } =
        await apiClient.getFileDownload(key);
      downloadBase64(content_base64, file_name || name, content_type);
    } catch (error) {
      setDataError(error instanceof Error ? error.message : "文件下载失败");
    }
  }

  async function deleteFile(key: string, name: string) {
    if (!apiClient || deletingKey) {
      return;
    }
    if (!window.confirm(`确定删除文件「${name}」？此操作不可撤销。`)) {
      return;
    }
    try {
      setDeletingKey(key);
      setDataError("");
      await apiClient.deleteDataFile(key);
      await refreshData(dataPrefix);
    } catch (error) {
      setDataError(error instanceof Error ? error.message : "文件删除失败");
    } finally {
      setDeletingKey("");
    }
  }

  useEffect(() => {
    if (apiClient) {
      void refreshTasks();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiClient]);

  async function refreshImages() {
    if (!apiClient) {
      return;
    }
    try {
      setImagesLoading(true);
      setImageError("");
      setImageListing(await apiClient.listImages());
    } catch (error) {
      setImageError(error instanceof Error ? error.message : "镜像列表加载失败");
    } finally {
      setImagesLoading(false);
    }
  }

  useEffect(() => {
    if (apiClient && menu === "data" && dataListing === null && !dataLoading) {
      void refreshData(dataPrefix);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiClient, menu]);

  useEffect(() => {
    if (apiClient && menu === "images" && imageListing === null && !imagesLoading) {
      void refreshImages();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiClient, menu]);

  useEffect(() => {
    if (apiClient && menu === "models" && models.length === 0 && !modelsLoading) {
      void refreshModels();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiClient, menu]);

  useEffect(() => {
    if (
      apiClient &&
      menu === "infer" &&
      inferTasks.length === 0 &&
      !inferTasksLoading
    ) {
      void refreshInferTasks();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiClient, menu]);

  async function loadLogs(taskId: string, executionId?: string) {
    if (!apiClient) {
      return;
    }
    try {
      setLogsLoading(true);
      setTaskLogError("");
      setTaskLogs(await apiClient.getLogs(taskId, executionId));
    } catch (error) {
      setTaskLogError(error instanceof Error ? error.message : "日志加载失败");
    } finally {
      setLogsLoading(false);
    }
  }

  async function loadEvaluation(taskId: string, executionId?: string) {
    if (!apiClient) {
      return;
    }
    try {
      setEvaluationLoading(true);
      setEvaluationError("");
      setEvaluation(await apiClient.getEvaluation(taskId, executionId));
    } catch (error) {
      setEvaluationError(error instanceof Error ? error.message : "训练结果加载失败");
    } finally {
      setEvaluationLoading(false);
    }
  }

  async function cancelTask(taskId: string) {
    if (!apiClient) {
      return;
    }
    try {
      setCancelling(true);
      await apiClient.cancelTask(taskId);
      await refreshTasks();
    } catch (error) {
      setTaskListError(error instanceof Error ? error.message : "取消任务失败");
    } finally {
      setCancelling(false);
    }
  }

  async function deployTask(taskId: string) {
    if (!apiClient) {
      return;
    }
    try {
      setDeploying(true);
      await apiClient.deployTask(taskId);
      await refreshTasks();
    } catch (error) {
      setTaskListError(error instanceof Error ? error.message : "部署失败");
    } finally {
      setDeploying(false);
    }
  }

  async function submitTask(
    payload: ConfirmedTaskSubmission,
  ): Promise<SubmissionResultViewModel> {
    if (!apiClient) {
      throw new Error("运行时配置缺失，无法提交");
    }
    const response = await apiClient.submitTask(payload);
    await refreshTasks();
    return {
      taskId: response.task_id,
      status: response.status,
      message: response.message,
    };
  }

  function openTask(taskId: string) {
    setSelectedTaskId(taskId);
    setTaskLogs(null);
    setTaskLogError("");
    setEvaluation(null);
    setEvaluationError("");
  }

  function renderTasks() {
    if (selectedTask) {
      return (
        <TaskDetailView
          task={selectedTask}
          logs={taskLogs}
          logsLoading={logsLoading}
          logError={taskLogError}
          onBack={() => setSelectedTaskId("")}
          onLoadLogs={(executionId?: string) =>
            void loadLogs(selectedTask.task_id, executionId)
          }
          onCancel={() => void cancelTask(selectedTask.task_id)}
          cancelling={cancelling}
          onDeploy={() => void deployTask(selectedTask.task_id)}
          deploying={deploying}
          evaluation={evaluation}
          evaluationLoading={evaluationLoading}
          evaluationError={evaluationError}
          onLoadEvaluation={(executionId?: string) =>
            void loadEvaluation(selectedTask.task_id, executionId)
          }
        />
      );
    }
    return (
      <TaskListPanel
        tasks={tasks}
        onSelectTask={openTask}
        onRefresh={() => void refreshTasks()}
        onNewTask={() => setWizardOpen(true)}
        isLoading={tasksLoading}
        errorMessage={taskListError}
      />
    );
  }

  function renderInfer() {
    if (selectedInferTask && apiClient) {
      return (
        <InferTaskDetailView
          task={selectedInferTask}
          apiClient={apiClient}
          onBack={() => setSelectedInferTaskId("")}
        />
      );
    }
    return (
      <InferTaskListPanel
        tasks={inferTasks}
        onSelectTask={setSelectedInferTaskId}
        onRefresh={() => void refreshInferTasks()}
        onNewTask={() => setInferWizardOpen(true)}
        isLoading={inferTasksLoading}
        errorMessage={inferTaskListError}
      />
    );
  }

  function renderModels() {
    if (selectedModel) {
      return (
        <ModelDetailView model={selectedModel} onBack={() => setSelectedModelId("")} />
      );
    }
    return (
      <ModelListPanel
        models={models}
        onSelectModel={setSelectedModelId}
        onRefresh={() => void refreshModels()}
        isLoading={modelsLoading}
        errorMessage={modelListError}
      />
    );
  }

  function renderData() {
    return (
      <DataFilePanel
        listing={dataListing}
        isLoading={dataLoading}
        errorMessage={dataError}
        onRefresh={() => void refreshData(dataPrefix)}
        onUpload={() => setUploadOpen(true)}
        onEnterFolder={(folder) => void refreshData(folder)}
        onNavigatePrefix={(prefix) => void refreshData(prefix)}
        onDownload={(key, name) => void downloadFile(key, name)}
        onDelete={(key, name) => void deleteFile(key, name)}
        deletingKey={deletingKey}
      />
    );
  }

  function renderImages() {
    if (selectedImage) {
      return (
        <ImageDetailView
          image={selectedImage}
          onBack={() => setSelectedImageTag("")}
        />
      );
    }
    return (
      <ImageListPanel
        listing={imageListing}
        isLoading={imagesLoading}
        errorMessage={imageError}
        onRefresh={() => void refreshImages()}
        onNewImage={() => setImageBuildOpen(true)}
        onSelect={setSelectedImageTag}
      />
    );
  }

  const isAdmin = user?.role === "admin";

  function renderContent() {
    if (menu === "tasks") return renderTasks();
    if (menu === "infer") return renderInfer();
    if (menu === "models") return renderModels();
    if (menu === "data") return renderData();
    // Images (训练Agent镜像) is admin-only; guard defensively even though the
    // menu item is hidden for non-admins.
    if (!isAdmin) return renderTasks();
    return renderImages();
  }

  // Gate the whole app behind login. Config must be loaded first so the API
  // client (used by the login form) exists.
  if (!user) {
    if (!apiClient) {
      return (
        <div className="app-shell">
          <HeaderBar
            environmentLabel={config?.environmentLabel ?? "Config Missing"}
          />
          <main className="content-area">
            {configErrors.length > 0 ? (
              <div className="banner banner-error" data-testid="config-error-banner">
                {configErrors.map((message) => (
                  <div key={message} className="error-text">
                    {message}
                  </div>
                ))}
              </div>
            ) : (
              <div className="muted-text">加载中...</div>
            )}
          </main>
        </div>
      );
    }
    return <LoginPage apiClient={apiClient} onLoggedIn={handleLoggedIn} />;
  }

  return (
    <div className="app-shell">
      <HeaderBar
        environmentLabel={config?.environmentLabel ?? "Config Missing"}
        user={user}
        apiClient={apiClient}
        onLogout={handleLogout}
      />
      <div className="shell-body">
        <Sidebar
          active={menu}
          isAdmin={isAdmin}
          onSelect={(key) => {
            setMenu(key);
            setSelectedTaskId("");
            setSelectedModelId("");
            setSelectedInferTaskId("");
          }}
        />
        <main className="content-area" data-testid="content-area">
          {configErrors.length > 0 ? (
            <div className="banner banner-error" data-testid="config-error-banner">
              {configErrors.map((message) => (
                <div key={message} className="error-text">
                  {message}
                </div>
              ))}
            </div>
          ) : null}
          {renderContent()}
        </main>
      </div>

      <Modal
        open={wizardOpen}
        title="新建训练任务"
        onClose={() => setWizardOpen(false)}
        testId="task-wizard-modal"
      >
        {effectiveConfig ? (
          <TaskWizard
            config={effectiveConfig}
            apiClient={apiClient!}
            onSubmit={submitTask}
            onFinished={() => setWizardOpen(false)}
          />
        ) : null}
      </Modal>

      <Modal
        open={inferWizardOpen}
        title="新建推理任务"
        onClose={() => setInferWizardOpen(false)}
        testId="infer-task-wizard-modal"
      >
        {effectiveConfig ? (
          <InferTaskWizard
            config={effectiveConfig}
            apiClient={apiClient!}
            onSubmit={submitInferTask}
            onFinished={() => setInferWizardOpen(false)}
          />
        ) : null}
      </Modal>

      {apiClient ? (
        <DataUploadDialog
          open={uploadOpen}
          apiClient={apiClient}
          currentPrefix={dataPrefix}
          existingFolders={dataListing?.folders ?? []}
          onClose={() => setUploadOpen(false)}
          onUploaded={() => void refreshData(dataPrefix)}
        />
      ) : null}

      {apiClient ? (
        <ImageBuildDialog
          open={imageBuildOpen}
          apiClient={apiClient}
          onClose={() => setImageBuildOpen(false)}
          onStarted={() => void refreshImages()}
        />
      ) : null}
    </div>
  );
}
