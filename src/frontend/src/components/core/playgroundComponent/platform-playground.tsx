import { Maximize2, Minimize2, Send, Square, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { SanitizedMarkdown } from "@/components/core/sanitizedMarkdown";
import { Button } from "@/components/ui/button";
import { useSimpleSidebar } from "@/components/ui/simple-sidebar";
import { BuildStatus } from "@/constants/enums";
import { api } from "@/controllers/API/api";
import { getURL } from "@/controllers/API/helpers/constants";
import { buildFlowUpdatePayload } from "@/hooks/flows/save-payload";
import useAuthStore from "@/stores/authStore";
import useFlowStore from "@/stores/flowStore";
import useFlowsManagerStore from "@/stores/flowsManagerStore";
import { usePlaygroundStore } from "@/stores/playgroundStore";
import type { WorkflowPublicationState } from "@/types/workflow-publication";
import { extractApiErrorMessages } from "@/utils/apiError";
import {
  type PlaygroundEvent,
  streamPlatformPlayground,
} from "./platform-stream";

type Step = {
  id: string;
  status: "running" | "finished" | "error" | "cancelled";
  duration?: number;
};
type Turn = {
  id: string;
  input?: string;
  status: "running" | "succeeded" | "failed" | "cancelled";
  steps: Step[];
  outputs?: { node_id: string; text: string }[];
  error?: string;
};

export function PlatformPlayground({
  isFullscreen,
  setIsFullscreen,
}: {
  isFullscreen: boolean;
  setIsFullscreen: (value: boolean) => void;
}) {
  const { open, setOpen, setWidth } = useSimpleSidebar();
  const owner = useAuthStore((state) => state.userData?.id);
  const actions = useAuthStore((state) => state.platformWorkflowActions);
  const current = useFlowStore((state) => state.currentFlow);
  const saved = useFlowsManagerStore((state) => state.currentFlow);
  const nodes = useFlowStore((state) => state.nodes);
  const edges = useFlowStore((state) => state.edges);
  const edited = useFlowStore((state) => state.userEditedSinceLoad);
  const building = useFlowStore((state) => state.isBuilding);
  const canDraft = !!actions?.includes("read") && actions.includes("dry_run");
  const canPublished = !!actions?.includes("read") && actions.includes("run");
  const [mode, setMode] = useState<"draft" | "published">(
    canDraft ? "draft" : "published",
  );
  const [publication, setPublication] =
    useState<WorkflowPublicationState | null>(null);
  const [loading, setLoading] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [input, setInput] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const waiting = useRef<{
    controller: AbortController;
    id: string;
    canvas: boolean;
    flowId: string;
  } | null>(null);
  const textArea = useRef<HTMLTextAreaElement>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const canvasFlow = useRef<string | null>(null);
  const sameFlow =
    !!owner && !!saved && saved.user_id === owner && current?.id === saved.id;
  const dirty =
    !!current &&
    buildFlowUpdatePayload({
      flow: current,
      persisted: saved,
      flows: undefined,
      live: { nodes, edges },
      userEdited: edited,
    }).data !== undefined;
  const permitted = mode === "draft" ? canDraft : canPublished;
  const version =
    mode === "draft" ? saved?.version_token : publication?.current?.id;
  const blockedDraft = mode === "draft" && dirty;
  const hasInput = nodes.some((node) =>
    ["TextInput", "ChatInput"].includes(node.data.type),
  );

  const clearActive = () => {
    const active = waiting.current;
    waiting.current = null;
    usePlaygroundStore.getState().setRequestPlatformStop(null);
    active?.controller.abort();
    if (
      active?.canvas &&
      useFlowStore.getState().currentFlow?.id === active.flowId
    ) {
      const flow = useFlowStore.getState();
      const pending = Object.entries(flow.flowBuildStatus)
        .filter(([, value]) => value.status === BuildStatus.BUILDING)
        .map(([id]) => id);
      flow.updateBuildStatus(pending, BuildStatus.INACTIVE);
      flow.clearAndSetEdgesRunning([]);
      flow.setIsBuilding(false);
    }
  };

  useEffect(() => {
    clearActive();
    if (canvasFlow.current === current?.id)
      useFlowStore.getState().setFlowPool({});
    canvasFlow.current = null;
    setBusy(false);
    setTurns([]);
    setInput("");
    setNotice("");
  }, [owner, current?.id, mode, version, permitted, sameFlow, blockedDraft]);
  useEffect(() => {
    if (!open && waiting.current) {
      clearActive();
      setBusy(false);
      setTurns((items) =>
        items.map((turn) =>
          turn.status === "running"
            ? {
                ...turn,
                status: "failed",
                error: "窗口已关闭，本次结果不再接收。",
              }
            : turn,
        ),
      );
    }
    if (open) textArea.current?.focus();
  }, [open]);
  useEffect(() => () => clearActive(), []);
  useEffect(() => {
    bottom.current?.scrollIntoView?.({ block: "end" });
  }, [turns]);
  useEffect(() => {
    if (!open || mode !== "published" || !sameFlow || !canPublished || !saved)
      return;
    const controller = new AbortController();
    setLoading(true);
    setNotice("");
    setPublication(null);
    void api
      .get<WorkflowPublicationState>(
        `${getURL("PLATFORM_WORKFLOW", {}, true)}/flows/${encodeURIComponent(saved.id)}/publication`,
        { signal: controller.signal },
      )
      .then(({ data }) => {
        if (!controller.signal.aborted) setPublication(data);
      })
      .catch((failure) => {
        if (!controller.signal.aborted)
          setNotice(extractApiErrorMessages(failure).join("；"));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [open, mode, owner, saved?.id, sameFlow, canPublished, refresh]);

  const send = async () => {
    if (
      !sameFlow ||
      !saved ||
      !version ||
      !permitted ||
      blockedDraft ||
      busy ||
      loading ||
      building
    )
      return;
    if (new TextEncoder().encode(input).length > 65536) {
      setNotice("输入不能超过 64 KiB。");
      return;
    }
    const controller = new AbortController();
    const id = crypto.randomUUID();
    // 线上快照不同于当前画布时, 只在对话内显示其节点过程, 避免标错草稿节点。
    const canvas =
      mode === "draft" ||
      (!dirty &&
        publication?.current?.source_version_token === saved.version_token);
    const active = { controller, id, canvas, flowId: saved.id };
    waiting.current = active;
    usePlaygroundStore.getState().setRequestPlatformStop(() => void stop());
    setBusy(true);
    setNotice("");
    setTurns((items) => [
      ...items.slice(-19),
      { id, input: input || undefined, status: "running", steps: [] },
    ]);
    const chatInput = input || undefined;
    setInput("");
    const flow = useFlowStore.getState();
    if (canvas) {
      canvasFlow.current = saved.id;
      flow.updateBuildStatus(
        nodes.map((node) => node.id),
        BuildStatus.TO_BUILD,
      );
      flow.setFlowPool({});
      flow.clearAndSetEdgesRunning([]);
      flow.setIsBuilding(true);
    }
    const running = new Set<string>();
    const currentRun = () =>
      waiting.current === active &&
      !controller.signal.aborted &&
      useAuthStore.getState().userData?.id === owner &&
      !!useAuthStore.getState().platformWorkflowActions?.includes("read") &&
      !!useAuthStore
        .getState()
        .platformWorkflowActions?.includes(
          mode === "draft" ? "dry_run" : "run",
        ) &&
      useFlowStore.getState().currentFlow?.id === saved.id &&
      (mode !== "draft" ||
        useFlowsManagerStore.getState().currentFlow?.version_token === version);
    const update = (change: (turn: Turn) => Turn) =>
      setTurns((items) =>
        items.map((turn) => (turn.id === id ? change(turn) : turn)),
      );
    try {
      await streamPlatformPlayground(
        {
          flowId: saved.id,
          version,
          mode,
          input: chatInput,
          runId: id,
          signal: controller.signal,
        },
        (event: PlaygroundEvent) => {
          if (!currentRun()) return;
          const nodeId = event.node_id;
          if (
            (event.type === "node_started" || event.type === "node_finished") &&
            nodeId
          ) {
            const started = event.type === "node_started";
            update((turn) => ({
              ...turn,
              steps: started
                ? [...turn.steps, { id: nodeId, status: "running" }]
                : turn.steps.map((step) =>
                    step.id === nodeId
                      ? {
                          ...step,
                          status: "finished",
                          duration: event.duration_ms,
                        }
                      : step,
                  ),
            }));
            if (canvas && nodes.some((node) => node.id === nodeId)) {
              flow.updateBuildStatus(
                [nodeId],
                started ? BuildStatus.BUILDING : BuildStatus.BUILT,
              );
              if (started) running.add(nodeId);
              else running.delete(nodeId);
              flow.clearAndSetEdgesRunning([...running]);
            }
          } else if (event.type === "run_finished") {
            setNotice("");
            update((turn) => ({
              ...turn,
              status: "succeeded",
              outputs: event.outputs,
            }));
            if (canvas)
              for (const output of event.outputs ?? []) {
                const node = nodes.find((node) => node.id === output.node_id);
                const outputName = node?.data.node?.outputs?.[0]?.name;
                if (outputName)
                  flow.addDataToFlowPool(
                    {
                      id: output.node_id,
                      run_id: id,
                      valid: true,
                      inactivated_vertices: null,
                      next_vertices_ids: [],
                      top_level_vertices: [],
                      timestamp: new Date().toISOString(),
                      params: null,
                      messages: [],
                      artifacts: null,
                      data: {
                        results: { [outputName]: output.text },
                        outputs: {
                          [outputName]: { message: output.text, type: "text" },
                        },
                        logs: {},
                        messages: [],
                      },
                    },
                    output.node_id,
                  );
              }
          } else if (
            event.type === "run_error" ||
            event.type === "run_cancelled"
          ) {
            const cancelled = event.type === "run_cancelled";
            setNotice("");
            update((turn) => ({
              ...turn,
              status: cancelled ? "cancelled" : "failed",
              error: cancelled ? undefined : event.detail,
              steps: turn.steps.map((step) =>
                step.status === "running"
                  ? { ...step, status: cancelled ? "cancelled" : "error" }
                  : step,
              ),
            }));
            if (canvas)
              flow.updateBuildStatus(
                [...running],
                cancelled ? BuildStatus.INACTIVE : BuildStatus.ERROR,
              );
          }
        },
      );
    } catch (failure) {
      if (currentRun()) {
        update((turn) => ({
          ...turn,
          status: "failed",
          error: failure instanceof Error ? failure.message : "运行失败。",
          steps: turn.steps.map((step) =>
            step.status === "running" ? { ...step, status: "error" } : step,
          ),
        }));
        if (canvas) flow.updateBuildStatus([...running], BuildStatus.ERROR);
      }
    } finally {
      if (waiting.current === active) {
        waiting.current = null;
        usePlaygroundStore.getState().setRequestPlatformStop(null);
        setBusy(false);
        if (canvas) {
          flow.clearAndSetEdgesRunning([]);
          flow.setIsBuilding(false);
        }
      }
    }
  };
  const stop = async () => {
    const active = waiting.current;
    if (!active || !actions?.includes("stop")) return;
    try {
      await api.post(
        `${getURL("PLATFORM_WORKFLOW", {}, true)}/flows/${encodeURIComponent(active.flowId)}/runs/${active.id}/stop`,
        {},
      );
      if (waiting.current === active)
        setNotice("已请求停止，正在等待服务端确认。");
    } catch (failure) {
      if (waiting.current === active)
        setNotice(extractApiErrorMessages(failure).join("；"));
    }
  };

  return (
    <section
      aria-label="平台 Playground"
      className="relative flex h-full w-full flex-col bg-background"
      onKeyDown={(event) => {
        if (event.key === "Escape") setOpen(false);
      }}
    >
      <header className="flex flex-wrap items-center justify-between gap-2 border-b px-4 py-3">
        <div>
          <h2 className="text-base font-semibold">Playground</h2>
          <p className="text-xs text-muted-foreground">
            {saved?.name} · 聊天调试
          </p>
        </div>
        <div className="flex items-center gap-1">
          <Button
            size="icon"
            variant="ghost"
            aria-label={isFullscreen ? "退出全屏" : "全屏"}
            onClick={() => {
              setIsFullscreen(!isFullscreen);
              if (isFullscreen) setWidth(440);
            }}
          >
            {isFullscreen ? (
              <Minimize2 className="h-4 w-4" />
            ) : (
              <Maximize2 className="h-4 w-4" />
            )}
          </Button>
          <Button
            size="icon"
            variant="ghost"
            aria-label="关闭 Playground"
            onClick={() => setOpen(false)}
          >
            <X className="h-4 w-4" />
          </Button>
        </div>
      </header>
      <div className="flex flex-wrap items-center gap-2 border-b px-4 py-2">
        <select
          aria-label="执行版本"
          value={mode}
          disabled={busy}
          className="rounded-md border bg-background px-2 py-1 text-sm"
          onChange={(event) =>
            setMode(event.target.value as "draft" | "published")
          }
        >
          <option value="draft" disabled={!canDraft}>
            已保存草稿
          </option>
          <option value="published" disabled={!canPublished}>
            线上发布版本
          </option>
        </select>
        <span className="text-xs text-muted-foreground">
          {mode === "draft"
            ? `修订 ${version?.slice(0, 8) ?? "未保存"}`
            : loading
              ? "读取线上版本…"
              : publication?.current
                ? `线上版本 V${publication.current.version_number}`
                : "未发布或已下线"}
        </span>
        <Button
          size="sm"
          variant="ghost"
          disabled={busy}
          onClick={() => {
            setTurns([]);
            setNotice("");
          }}
        >
          清空对话
        </Button>
        {mode === "published" && (
          <Button
            size="sm"
            variant="ghost"
            disabled={busy || loading}
            onClick={() => setRefresh((value) => value + 1)}
          >
            更新版本
          </Button>
        )}
      </div>
      <div
        role="log"
        aria-label="对话与执行过程"
        aria-live="polite"
        className="min-h-0 flex-1 overflow-y-auto px-4 py-6"
      >
        <div className="mx-auto flex max-w-[744px] flex-col gap-6">
          {!turns.length && (
            <div className="py-12 text-center text-sm text-muted-foreground">
              <p className="mb-2 text-base font-medium text-foreground">
                在对话中验证工作流
              </p>
              <p>发送文本后查看回答和每个节点的执行状态。</p>
            </div>
          )}
          {turns.map((turn) => (
            <article key={turn.id} className="space-y-3">
              <div className="ml-auto max-w-[85%] whitespace-pre-wrap break-words rounded-2xl bg-muted px-4 py-3 text-sm">
                {turn.input ?? "使用节点中已保存的输入"}
              </div>
              <div className="rounded-lg border px-4 py-3">
                <p className="mb-2 text-xs font-medium text-muted-foreground">
                  {turn.status === "running"
                    ? "正在执行…"
                    : turn.status === "succeeded"
                      ? "执行完成"
                      : turn.status === "cancelled"
                        ? "执行已取消"
                        : "执行失败"}
                </p>
                {turn.outputs?.map((output) => (
                  <div key={output.node_id} className="mb-2 text-sm">
                    <SanitizedMarkdown
                      chatMessage={output.text}
                      isEmpty={output.text === ""}
                      emptyMessage="（空文本）"
                    />
                  </div>
                ))}
                {turn.error && (
                  <p role="alert" className="text-sm text-destructive">
                    {turn.error}
                  </p>
                )}
                {!!turn.steps.length && (
                  <details className="mt-3 border-t pt-2 text-xs">
                    <summary className="cursor-pointer font-medium">
                      执行过程
                    </summary>
                    <ol className="mt-2 space-y-2">
                      {turn.steps.map((step) => (
                        <li
                          key={step.id}
                          className="flex justify-between gap-3"
                        >
                          <span className="min-w-0 break-all">
                            {nodes.find((node) => node.id === step.id)?.data
                              .node?.display_name ?? step.id}
                          </span>
                          <span className="shrink-0 text-muted-foreground">
                            {step.status === "running"
                              ? "执行中"
                              : step.status === "finished"
                                ? `完成 · ${step.duration ?? 0} ms`
                                : step.status === "cancelled"
                                  ? "已取消"
                                  : "失败"}
                          </span>
                        </li>
                      ))}
                    </ol>
                  </details>
                )}
              </div>
            </article>
          ))}
          <div ref={bottom} />
        </div>
      </div>
      <footer className="border-t p-4">
        <div className="mx-auto max-w-[744px] space-y-2">
          {(!sameFlow ||
            !permitted ||
            blockedDraft ||
            (mode === "published" && !version && !loading)) && (
            <p role="status" className="text-xs text-muted-foreground">
              {!sameFlow
                ? "请加载当前账号拥有的流程。"
                : !permitted
                  ? "当前没有所选模式的执行权限。"
                  : blockedDraft
                    ? "画布有未保存的修改，请先保存后再调试。"
                    : "请先发布流程，再运行线上版本。"}
            </p>
          )}
          {notice && (
            <p role="status" className="text-xs text-muted-foreground">
              {notice}
            </p>
          )}
          <form
            className="flex items-end gap-2 rounded-xl border bg-background p-2"
            onSubmit={(event) => {
              event.preventDefault();
              void send();
            }}
          >
            <textarea
              ref={textArea}
              aria-label="聊天输入"
              data-testid="input-chat-playground"
              value={input}
              disabled={busy || !permitted || !sameFlow}
              rows={2}
              maxLength={65536}
              className="min-w-0 flex-1 resize-none bg-transparent px-2 py-1 text-sm outline-none"
              placeholder={
                hasInput
                  ? "输入消息，Enter 发送，Shift+Enter 换行"
                  : "留空以使用流程已保存的输入"
              }
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => {
                if (
                  event.key === "Enter" &&
                  !event.shiftKey &&
                  !event.nativeEvent.isComposing
                ) {
                  event.preventDefault();
                  void send();
                }
              }}
            />
            {busy ? (
              <Button
                type="button"
                aria-label="停止运行"
                disabled={!actions?.includes("stop")}
                onClick={() => void stop()}
              >
                <Square className="h-4 w-4" />
              </Button>
            ) : (
              <Button
                type="submit"
                aria-label="发送"
                disabled={
                  !sameFlow ||
                  !version ||
                  !permitted ||
                  blockedDraft ||
                  loading ||
                  building
                }
              >
                <Send className="h-4 w-4" />
              </Button>
            )}
          </form>
          <p className="text-xs text-muted-foreground">
            对话仅保留在本窗口，每次发送独立执行，不自动携带历史。关闭窗口会请求取消执行，已受理的模型调用可能仍计费。
          </p>
        </div>
      </footer>
    </section>
  );
}
