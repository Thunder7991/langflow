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
import useAutoSaveFlow from "@/hooks/flows/use-autosave-flow";
import useAuthStore from "@/stores/authStore";
import useFlowStore from "@/stores/flowStore";
import useFlowsManagerStore from "@/stores/flowsManagerStore";
import type {
  WorkflowPublication,
  WorkflowPublicationState,
} from "@/types/workflow-publication";
import { extractApiErrorMessages } from "@/utils/apiError";

/** 发布管理沿用编辑器保存队列；线上指针和草稿修订分别校验。 */
export default function PlatformAccessMenu() {
  const actions = useAuthStore((state) => state.platformWorkflowActions);
  const owner = useAuthStore((state) => state.userData?.id);
  const flow = useFlowsManagerStore((state) => state.currentFlow);
  const hasUnsavedGraph = useFlowStore((state) => {
    if (!flow || !state.currentFlow || state.currentFlow.id !== flow.id)
      return false;
    return (
      buildFlowUpdatePayload({
        flow: state.currentFlow,
        persisted: flow,
        flows: undefined,
        live: { nodes: state.nodes, edges: state.edges },
        userEdited: state.userEditedSinceLoad,
      }).data !== undefined
    );
  });
  const saveQueue = useAutoSaveFlow();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [publication, setPublication] =
    useState<WorkflowPublicationState | null>(null);
  const [history, setHistory] = useState<WorkflowPublication[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [confirmation, setConfirmation] = useState<
    "offline" | WorkflowPublication | null
  >(null);
  const [output, setOutput] = useState("");
  const [error, setError] = useState("");
  const generation = useRef(0);
  const pending = useRef(false);
  const loadSequence = useRef(0);
  const granted = actions?.join(",");
  const base = flow
    ? `${getURL("PLATFORM_WORKFLOW", {}, true)}/flows/${encodeURIComponent(flow.id)}`
    : "";
  const sameIdentity = (epoch: number, action: string) =>
    generation.current === epoch &&
    useAuthStore.getState().userData?.id === owner &&
    !!useAuthStore.getState().platformWorkflowActions?.includes(action) &&
    useFlowsManagerStore.getState().currentFlow?.id === flow?.id;

  useEffect(() => {
    generation.current += 1;
    pending.current = false;
    setBusy(false);
    setPublication(null);
    setHistory([]);
    setOffset(0);
    setConfirmation(null);
    setOutput("");
    setError("");
  }, [owner, flow?.id, granted]);
  useEffect(
    () => () => {
      generation.current += 1;
    },
    [],
  );

  useEffect(() => {
    if (
      !open ||
      !flow ||
      flow.user_id !== owner ||
      !actions?.includes("read") ||
      pending.current
    )
      return;
    const epoch = generation.current;
    const sequence = ++loadSequence.current;
    setLoading(true);
    setError("");
    void Promise.all([
      api.get<WorkflowPublicationState>(`${base}/publication`),
      api.get<{ items: WorkflowPublication[]; total: number }>(
        `${base}/publications`,
        { params: { offset, limit: 20 } },
      ),
    ])
      .then(([state, versions]) => {
        if (!sameIdentity(epoch, "read") || sequence !== loadSequence.current)
          return;
        setPublication(state.data);
        setHistory(versions.data.items);
        setTotal(versions.data.total);
      })
      .catch((failure) => {
        if (sameIdentity(epoch, "read") && sequence === loadSequence.current)
          setError(extractApiErrorMessages(failure).join("；"));
      })
      .finally(() => {
        if (generation.current === epoch && sequence === loadSequence.current)
          setLoading(false);
      });
  }, [open, owner, flow?.id, flow?.version_token, granted, offset]);

  const perform = async (
    action: "publish" | "offline" | "rollback" | "share" | "api_access",
    target?: WorkflowPublication,
  ) => {
    const permission = ["offline", "rollback"].includes(action)
      ? "publish"
      : action;
    if (
      !flow ||
      flow.user_id !== owner ||
      !actions?.includes(permission) ||
      pending.current
    )
      return;
    if (["publish", "offline", "rollback"].includes(action) && !publication)
      return;
    const epoch = generation.current;
    pending.current = true;
    loadSequence.current += 1;
    setLoading(false);
    setBusy(true);
    setError("");
    setOutput("");
    try {
      let response;
      if (action === "publish") {
        const editor = useFlowStore.getState().currentFlow;
        if (!editor || editor.id !== flow.id)
          throw new Error("当前画布与目标流程不一致，请重新打开流程。");
        if (actions.includes("update")) await saveQueue.enqueue(editor);
        if (!sameIdentity(epoch, permission)) return;
        const saved = useFlowsManagerStore.getState().currentFlow;
        const live = useFlowStore.getState();
        if (
          !saved?.version_token ||
          !live.currentFlow ||
          saved.id !== flow.id ||
          live.currentFlow.id !== flow.id
        )
          throw new Error("未能确认保存版本，未执行发布。");
        if (
          buildFlowUpdatePayload({
            flow: live.currentFlow,
            persisted: saved,
            flows: undefined,
            live: { nodes: live.nodes, edges: live.edges },
            userEdited: live.userEditedSinceLoad,
          }).data !== undefined
        )
          throw new Error("画布仍有未保存修改，请保存完成后再发布。");
        response = await api.post<WorkflowPublicationState>(
          `${base}/publish`,
          {
            published: true,
            publication_token: publication!.publication_token,
          },
          { headers: { "If-Match": saved.version_token } },
        );
      } else if (action === "offline") {
        response = await api.post<WorkflowPublicationState>(`${base}/publish`, {
          published: false,
          publication_token: publication!.publication_token,
        });
      } else if (action === "rollback") {
        if (!target) return;
        response = await api.post<WorkflowPublicationState>(
          `${base}/publications/${encodeURIComponent(target.id)}/activate`,
          { publication_token: publication!.publication_token },
        );
      } else {
        response = await api.get(
          `${base}/${action === "api_access" ? "api" : "share"}`,
        );
      }
      if (!sameIdentity(epoch, permission)) return;
      if (["publish", "offline", "rollback"].includes(action)) {
        setPublication(response.data);
        setConfirmation(null);
        // 发布已成功时，历史读取失败不能把成功误报为发布失败。
        try {
          const versions = await api.get(`${base}/publications`, {
            params: { offset: 0, limit: 20 },
          });
          if (sameIdentity(epoch, permission)) {
            setHistory(versions.data.items);
            setTotal(versions.data.total);
            setOffset(0);
          }
        } catch {
          if (sameIdentity(epoch, permission))
            setError("操作已成功，发布历史加载失败，请重新打开查看。");
        }
      } else
        setOutput(
          action === "share"
            ? response.data.url
            : JSON.stringify(response.data, null, 2),
        );
    } catch (failure) {
      if (sameIdentity(epoch, permission))
        setError(extractApiErrorMessages(failure).join("；"));
    } finally {
      if (generation.current === epoch) {
        pending.current = false;
        setBusy(false);
      }
    }
  };

  if (
    !actions ||
    !["publish", "share", "api_access"].some((action) =>
      actions.includes(action),
    )
  )
    return null;
  return (
    <>
      <Button
        data-testid="platform-access-menu-button"
        variant="ghost"
        size="sm"
        disabled={!flow || flow.user_id !== owner}
        onClick={() => setOpen(true)}
      >
        发布与访问
      </Button>
      <Dialog
        open={open}
        onOpenChange={(next) => {
          setOpen(next);
          if (!next) {
            generation.current += 1;
            loadSequence.current += 1;
            pending.current = false;
            setBusy(false);
            setLoading(false);
            setConfirmation(null);
            setOutput("");
            setError("");
          }
        }}
      >
        <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>发布与访问</DialogTitle>
            <DialogDescription>
              保存保留为草稿，发布后线上才更新。回滚保留当前草稿，下线停止接收新业务调用。
            </DialogDescription>
          </DialogHeader>
          {loading && <p role="status">正在读取发布状态…</p>}
          {publication && (
            <div className="rounded-lg border bg-muted/30 p-4">
              <div className="flex items-center justify-between gap-3">
                <p className="font-medium">
                  {publication.current
                    ? `线上版本 V${publication.current.version_number}`
                    : publication.status === "offline"
                      ? "已下线"
                      : "未发布"}
                </p>
                {(publication.has_unpublished_changes || hasUnsavedGraph) && (
                  <span className="rounded-full bg-warning px-2.5 py-1 text-xs text-warning-foreground">
                    有未发布修改
                  </span>
                )}
              </div>
              <p className="mt-2 text-sm text-muted-foreground">
                {publication.current
                  ? "业务运行使用该发布快照，画布修改需再次发布才生效。"
                  : "完成发布后，业务入口才能运行此流程。"}
              </p>
            </div>
          )}
          <div className="flex flex-wrap gap-2">
            {actions.includes("publish") && (
              <>
                <Button
                  disabled={busy || loading || !publication}
                  onClick={() => perform("publish")}
                >
                  {busy ? "正在处理…" : "保存并发布"}
                </Button>
                {publication?.current && (
                  <Button
                    variant="outline"
                    disabled={busy || loading}
                    onClick={() => setConfirmation("offline")}
                  >
                    下线
                  </Button>
                )}
              </>
            )}
            {actions.includes("share") && (
              <Button
                variant="outline"
                disabled={busy}
                onClick={() => perform("share")}
              >
                获取工作流链接
              </Button>
            )}
            {actions.includes("api_access") && (
              <Button
                variant="outline"
                disabled={busy}
                onClick={() => perform("api_access")}
              >
                查看 API 调用配置
              </Button>
            )}
          </div>
          {confirmation && (
            <div
              role="alertdialog"
              aria-label="确认发布操作"
              className="space-y-3 rounded-lg border p-4"
            >
              <p>
                {confirmation === "offline"
                  ? "确认下线？新业务调用将被拒绝，在途任务继续使用原发布快照。"
                  : `确认回滚至 V${confirmation.version_number}？当前草稿将保留。`}
              </p>
              <div className="flex gap-2">
                <Button
                  disabled={busy}
                  onClick={() =>
                    confirmation === "offline"
                      ? perform("offline")
                      : perform("rollback", confirmation)
                  }
                >
                  {confirmation === "offline" ? "确认下线" : "确认回滚"}
                </Button>
                <Button
                  variant="outline"
                  disabled={busy}
                  onClick={() => setConfirmation(null)}
                >
                  取消
                </Button>
              </div>
            </div>
          )}
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          {history.length > 0 && (
            <section className="space-y-3" aria-label="发布历史">
              <h3 className="text-sm font-medium">发布历史</h3>
              <div className="divide-y rounded-lg border">
                {history.map((release) => (
                  <div
                    key={release.id}
                    className="flex items-center justify-between gap-3 px-4 py-3"
                  >
                    <div>
                      <p className="text-sm font-medium">
                        V{release.version_number}
                        {release.id === publication?.current?.id
                          ? " · 当前线上"
                          : ""}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        {new Date(release.created_at).toLocaleString()}
                      </p>
                    </div>
                    {actions.includes("publish") &&
                      release.id !== publication?.current?.id && (
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={busy || loading}
                          onClick={() => setConfirmation(release)}
                        >
                          回滚至 V{release.version_number}
                        </Button>
                      )}
                  </div>
                ))}
              </div>
              {total > 20 && (
                <div className="flex items-center justify-between text-sm">
                  <span>共 {total} 个发布版本</span>
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={busy || loading || offset === 0}
                      onClick={() => setOffset(offset - 20)}
                    >
                      上一页
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={busy || loading || offset + 20 >= total}
                      onClick={() => setOffset(offset + 20)}
                    >
                      下一页
                    </Button>
                  </div>
                </div>
              )}
            </section>
          )}
          {output && (
            <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-all rounded border p-3 text-sm">
              {output}
            </pre>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
