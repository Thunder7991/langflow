import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { api } from "@/controllers/API/api";
import { getURL } from "@/controllers/API/helpers/constants";
import { buildFlowUpdatePayload } from "@/hooks/flows/save-payload";
import useAuthStore from "@/stores/authStore";
import useFlowStore from "@/stores/flowStore";
import useFlowsManagerStore from "@/stores/flowsManagerStore";
import type { WorkflowPublicationState } from "@/types/workflow-publication";
import { extractApiErrorMessages } from "@/utils/apiError";

type TextRun = {
  status: "succeeded";
  run_id: string;
  flow_id: string;
  version_token: string;
  publication_id?: string;
  publication_version?: number;
  outputs: { node_id: string; text: string }[];
};

export default function PlatformDryRunButton({
  action = "dry_run",
  open: controlledOpen,
  onOpenChange,
  onCloseAutoFocus,
  showTrigger = true,
}: {
  action?: "dry_run" | "run";
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  onCloseAutoFocus?: (event: Event) => void;
  showTrigger?: boolean;
}) {
  const owner = useAuthStore((state) => state.userData?.id);
  const canRun = useAuthStore(
    (state) =>
      (state.platformWorkflowActions?.includes("read") &&
        state.platformWorkflowActions.includes(action)) ??
      false,
  );
  const current = useFlowStore((state) => state.currentFlow);
  const saved = useFlowsManagerStore((state) => state.currentFlow);
  const nodes = useFlowStore((state) => state.nodes);
  const usesModel = nodes.some((node) => node.data?.type === "PlatformModel");
  const label =
    action === "run" ? "工作流运行" : usesModel ? "平台模型调试" : "纯文本调试";
  const edges = useFlowStore((state) => state.edges);
  const edited = useFlowStore((state) => state.userEditedSinceLoad);
  const version = saved?.version_token;
  const sameFlow =
    !!owner &&
    !!current &&
    current.id === saved?.id &&
    saved?.user_id === owner;
  // 沿用保存逻辑判断图变化，不把选中节点、视口和加载时模板刷新误当成用户编辑。
  const dirty =
    !!current &&
    buildFlowUpdatePayload({
      flow: current,
      persisted: saved,
      flows: undefined,
      live: { nodes, edges },
      userEdited: edited,
    }).data !== undefined;
  const [internalOpen, setOpen] = useState(false);
  const open = controlledOpen ?? internalOpen;
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<TextRun | null>(null);
  const [error, setError] = useState("");
  const [publication, setPublication] =
    useState<WorkflowPublicationState | null>(null);
  const [loadingPublication, setLoadingPublication] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const executionVersion =
    action === "run" ? publication?.current?.id : version;
  const executionDirty = action === "run" ? false : dirty;
  const executionUsesModel = action === "run" ? false : usesModel;
  const waiting = useRef<AbortController | null>(null);
  const runId = useRef<string | null>(null);
  const canStop = useAuthStore(
    (state) => state.platformWorkflowActions?.includes("stop") ?? false,
  );

  useEffect(() => {
    if (waiting.current) {
      waiting.current.abort();
      waiting.current = null;
      setBusy(false);
      setError("流程、版本或权限已变化，本次结果不再显示。");
    }
    setResult(null);
  }, [
    owner,
    current?.id,
    executionVersion,
    canRun,
    executionDirty,
    sameFlow,
    executionUsesModel,
  ]);
  useEffect(() => () => waiting.current?.abort(), []);

  useEffect(() => {
    if (action !== "run" || !open || !sameFlow || !saved || !canRun) return;
    const controller = new AbortController();
    setPublication(null);
    setLoadingPublication(true);
    void api
      .get<WorkflowPublicationState>(
        `${getURL("PLATFORM_WORKFLOW", {}, true)}/flows/${encodeURIComponent(saved.id)}/publication`,
        { signal: controller.signal },
      )
      .then((response) => {
        if (!controller.signal.aborted) setPublication(response.data);
      })
      .catch((failure) => {
        if (!controller.signal.aborted)
          setError(extractApiErrorMessages(failure).join("；"));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoadingPublication(false);
      });
    return () => controller.abort();
  }, [action, open, sameFlow, saved?.id, owner, canRun, refresh]);

  const handleOpen = (next: boolean) => {
    if (controlledOpen === undefined) setOpen(next);
    onOpenChange?.(next);
    if (!next) {
      waiting.current?.abort();
      waiting.current = null;
      setBusy(false);
      setResult(null);
      setError("");
    }
  };

  const runSaved = async () => {
    if (
      !sameFlow ||
      !saved ||
      !executionVersion ||
      !canRun ||
      executionDirty ||
      busy
    )
      return;
    const actor = useAuthStore.getState();
    if (
      actor.userData?.id !== owner ||
      !actor.platformWorkflowActions?.includes("read") ||
      !actor.platformWorkflowActions?.includes(action)
    )
      return;
    const controller = new AbortController();
    waiting.current = controller;
    runId.current = action === "run" ? crypto.randomUUID() : null;
    setBusy(true);
    setError("");
    setResult(null);
    try {
      const response = await api.post<TextRun>(
        action === "run"
          ? `${getURL("PLATFORM_WORKFLOW", {}, true)}/flows/${encodeURIComponent(saved.id)}/run`
          : `${getURL("PLATFORM_FLOWS", {}, true)}/${encodeURIComponent(saved.id)}/dry-run`,
        action === "run" ? { run_id: runId.current } : {},
        {
          signal: controller.signal,
          headers: { "If-Match": executionVersion },
          timeout: usesModel || action === "run" ? 60000 : 15000,
        },
      );
      if (controller.signal.aborted || waiting.current !== controller) return;
      const latest = useAuthStore.getState();
      if (
        latest.userData?.id !== owner ||
        !latest.platformWorkflowActions?.includes("read") ||
        !latest.platformWorkflowActions?.includes(action) ||
        useFlowStore.getState().currentFlow?.id !== saved.id
      )
        return;
      if (
        action !== "run" &&
        useFlowsManagerStore.getState().currentFlow?.version_token !== version
      ) {
        // 读取最新修订可能早于 React 订阅刷新；丢弃旧结果时也必须给出可核对原因。
        setError(
          "运行期间草稿版本已变化，本次结果不再显示；请核对版本后再运行。",
        );
        return;
      }
      if ((response.data as { status: string }).status === "cancelled") {
        setError("服务端执行已取消；已发出的模型请求仍可能产生费用。");
        return;
      }
      if (
        response.data.status !== "succeeded" ||
        (action === "run" && response.data.run_id !== runId.current) ||
        response.data.flow_id !== saved.id ||
        response.data.version_token !== executionVersion ||
        (action === "run" && response.data.publication_id !== executionVersion)
      )
        throw new Error("未能确认本次运行结果，请核对保存版本后再试。");
      setResult(response.data);
    } catch (failure) {
      if (controller.signal.aborted || waiting.current !== controller) return;
      const status = (failure as { response?: { status?: number } }).response
        ?.status;
      const runAlreadyAccepted =
        (
          failure as {
            response?: { data?: { detail?: { code?: string } } };
          }
        ).response?.data?.detail?.code === "platform_model_run_accepted";
      const workflowAlreadyAccepted =
        (failure as { response?: { data?: { detail?: { code?: string } } } })
          .response?.data?.detail?.code === "workflow_run_accepted";
      const modelState = (
        failure as { response?: { data?: { detail?: { code?: string } } } }
      ).response?.data?.detail?.code;
      setError(
        status === 409 &&
          (modelState === "TENANT_DEFAULT_UNSET" ||
            modelState === "MODEL_UNAVAILABLE")
          ? extractApiErrorMessages(failure).join("；")
          : status === 409 && workflowAlreadyAccepted
            ? "本次运行 ID 已受理，结果可能未知。请查询原运行，不要自动重试。"
            : status === 409 && runAlreadyAccepted
              ? "本次模型运行已受理，结果可能未知。请勿自动重试；核对后再发起新运行。"
              : status === 409
                ? "服务端流程版本已变化，请先加载最新版本再运行；当前草稿保留。"
                : status === 429
                  ? "当前已有一次调试正在执行，请稍后重试。"
                  : usesModel &&
                      (status === 502 || status === 503 || status === 504)
                    ? "平台模型调试失败或结果未知，重复运行可能再次计费；本次请求不会自动重试。"
                    : status === 504
                      ? "受控调试超时。模型调用结果可能未知，请核对后再发起新运行。"
                      : extractApiErrorMessages(failure).join("；"),
      );
    } finally {
      if (waiting.current === controller) {
        waiting.current = null;
        setBusy(false);
      }
    }
  };

  const stopSaved = async () => {
    const activeRun = runId.current;
    if (!saved || !activeRun || !busy || !canStop) return;
    try {
      await api.post(
        `${getURL("PLATFORM_WORKFLOW", {}, true)}/flows/${saved.id}/runs/${activeRun}/stop`,
        {},
      );
      if (runId.current === activeRun)
        setError("已请求服务端停止；供应商已受理的请求可能仍计费。");
    } catch (failure) {
      if (runId.current === activeRun)
        setError(extractApiErrorMessages(failure).join("；"));
    }
  };

  return (
    <>
      {canRun && showTrigger && (
        <Button
          size="sm"
          data-testid={
            action === "run" ? "platform-run-button" : "platform-dry-run-button"
          }
          variant="ghost"
          disabled={!canRun || !sameFlow || (action !== "run" && !version)}
          title={action === "run" ? "运行当前线上发布版本" : "运行已保存的草稿"}
          onClick={() => handleOpen(true)}
        >
          {label}
        </Button>
      )}
      <Dialog open={open} onOpenChange={handleOpen}>
        <DialogContent
          className="max-h-[80vh] max-w-2xl overflow-y-auto"
          onCloseAutoFocus={onCloseAutoFocus}
        >
          <DialogHeader>
            <DialogTitle>{label}</DialogTitle>
            <DialogDescription>
              {action === "run"
                ? "运行当前线上发布快照，限时 45 秒。保存草稿不会改变线上版本；再次发布不改变已受理的任务。"
                : usesModel
                  ? "仅支持文本输入 → 平台模型 → 文本输出，运行已保存版本。模型连接需有凭据使用权限及 secret_use 能力，供应商调用限时 30 秒，不自动重试。"
                  : "仅支持文本输入 → 文本输出，运行已保存版本，执行阶段限时 10 秒。"}
              文件解析、工具调用和 MCP 尚未开放。
            </DialogDescription>
          </DialogHeader>
          {loadingPublication && <p role="status">正在读取线上版本…</p>}
          {action === "run" && publication && (
            <p role="status">
              {publication.current
                ? `本次将运行线上版本 V${publication.current.version_number}`
                : "流程未发布或已下线，请先发布后再进行业务运行。"}
            </p>
          )}
          {dirty && action !== "run" && (
            <p role="status">画布有未保存的图变化，请先保存后再运行。</p>
          )}
          {!canRun && (
            <p role="status">当前没有工作流调试权限，画布内容已保留。</p>
          )}
          {canRun && (
            <Button
              disabled={
                !sameFlow ||
                !executionVersion ||
                executionDirty ||
                busy ||
                loadingPublication
              }
              onClick={runSaved}
            >
              {action === "run" ? "开始运行" : "运行已保存版本"}
            </Button>
          )}
          {busy && action === "run" && canStop && (
            <Button variant="outline" onClick={stopSaved}>
              停止服务端运行
            </Button>
          )}
          {busy && (
            <p role="status">
              正在运行…关闭窗口仅停止等待，不表示服务端任务已经停止。
            </p>
          )}
          {error && <p role="alert">{error}</p>}
          {action === "run" && !busy && (
            <Button
              variant="outline"
              disabled={loadingPublication}
              onClick={() => {
                setError("");
                setRefresh(refresh + 1);
              }}
            >
              更新状态
            </Button>
          )}
          {result && (
            <div className="space-y-3">
              <p role="status">
                {action === "run"
                  ? `运行成功，结果来自发布版本 V${result.publication_version}。`
                  : "运行成功，结果来自本次运行的已保存版本。"}
              </p>
              {result.outputs.map((output) => (
                <pre
                  key={output.node_id}
                  className="max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-md border p-3 text-sm"
                >
                  {output.text === "" ? "（空文本）" : output.text}
                </pre>
              ))}
            </div>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
