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
import { extractApiErrorMessages } from "@/utils/apiError";

type TextRun = {
  status: "succeeded";
  run_id: string;
  flow_id: string;
  version_token: string;
  outputs: { node_id: string; text: string }[];
};

export default function PlatformDryRunButton() {
  const owner = useAuthStore((state) => state.userData?.id);
  const canRun = useAuthStore(
    (state) =>
      (state.platformWorkflowActions?.includes("read") &&
        state.platformWorkflowActions.includes("dry_run")) ??
      false,
  );
  const current = useFlowStore((state) => state.currentFlow);
  const saved = useFlowsManagerStore((state) => state.currentFlow);
  const nodes = useFlowStore((state) => state.nodes);
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
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<TextRun | null>(null);
  const [error, setError] = useState("");
  const waiting = useRef<AbortController | null>(null);

  useEffect(() => {
    if (waiting.current) {
      waiting.current.abort();
      waiting.current = null;
      setBusy(false);
      setError("流程、版本或权限已变化，本次结果不再显示。");
    }
    setResult(null);
  }, [owner, current?.id, version, canRun, dirty, sameFlow]);
  useEffect(() => () => waiting.current?.abort(), []);

  const handleOpen = (next: boolean) => {
    setOpen(next);
    if (!next) {
      waiting.current?.abort();
      waiting.current = null;
      setBusy(false);
      setResult(null);
      setError("");
    }
  };

  const runSaved = async () => {
    if (!sameFlow || !saved || !version || !canRun || dirty || busy) return;
    const actor = useAuthStore.getState();
    if (
      actor.userData?.id !== owner ||
      !actor.platformWorkflowActions?.includes("read") ||
      !actor.platformWorkflowActions?.includes("dry_run")
    )
      return;
    const controller = new AbortController();
    waiting.current = controller;
    setBusy(true);
    setError("");
    setResult(null);
    try {
      const response = await api.post<TextRun>(
        `${getURL("PLATFORM_FLOWS", {}, true)}/${encodeURIComponent(saved.id)}/dry-run`,
        {},
        {
          signal: controller.signal,
          headers: { "If-Match": version },
          timeout: 15000,
        },
      );
      if (controller.signal.aborted || waiting.current !== controller) return;
      const latest = useAuthStore.getState();
      if (
        latest.userData?.id !== owner ||
        !latest.platformWorkflowActions?.includes("read") ||
        !latest.platformWorkflowActions?.includes("dry_run") ||
        useFlowStore.getState().currentFlow?.id !== saved.id ||
        useFlowsManagerStore.getState().currentFlow?.version_token !== version
      )
        return;
      if (
        response.data.status !== "succeeded" ||
        response.data.flow_id !== saved.id ||
        response.data.version_token !== version
      )
        throw new Error("未能确认本次运行结果，请核对保存版本后再试。");
      setResult(response.data);
    } catch (failure) {
      if (controller.signal.aborted || waiting.current !== controller) return;
      const status = (failure as { response?: { status?: number } }).response
        ?.status;
      setError(
        status === 409
          ? "服务端流程版本已变化，请先加载最新版本再运行；当前草稿保留。"
          : status === 429
            ? "当前已有一次调试正在执行，请稍后重试。"
            : status === 504
              ? "纯文本调试超时，请稍后重试。"
              : extractApiErrorMessages(failure).join("；"),
      );
    } finally {
      if (waiting.current === controller) {
        waiting.current = null;
        setBusy(false);
      }
    }
  };

  return (
    <>
      <Button
        size="sm"
        variant="ghost"
        disabled={!canRun || !sameFlow || !version}
        title={!canRun ? "需要工作流调试权限" : "运行已保存的纯文本流程"}
        onClick={() => handleOpen(true)}
      >
        纯文本调试
      </Button>
      <Dialog open={open} onOpenChange={handleOpen}>
        <DialogContent className="max-h-[80vh] max-w-2xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>纯文本调试</DialogTitle>
            <DialogDescription>
              本轮仅支持文本输入连接文本输出，运行已保存版本，执行阶段限时 10
              秒。模型、文件解析和 MCP 尚未开放。
            </DialogDescription>
          </DialogHeader>
          {dirty && (
            <p role="status">画布有未保存的图变化，请先保存后再运行。</p>
          )}
          {!canRun && (
            <p role="status">当前没有工作流调试权限，画布内容已保留。</p>
          )}
          <Button
            disabled={!canRun || !sameFlow || !version || dirty || busy}
            onClick={runSaved}
          >
            运行已保存版本
          </Button>
          {busy && (
            <p role="status">
              正在运行…关闭窗口仅停止等待，不表示服务端任务已经停止。
            </p>
          )}
          {error && <p role="alert">{error}</p>}
          {result && (
            <div className="space-y-3">
              <p role="status">运行成功，结果来自本次运行的已保存版本。</p>
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
