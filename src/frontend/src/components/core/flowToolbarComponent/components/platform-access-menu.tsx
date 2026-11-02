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
import useAuthStore from "@/stores/authStore";
import useFlowsManagerStore from "@/stores/flowsManagerStore";
import { extractApiErrorMessages } from "@/utils/apiError";

/** 平台发布、链接与 API 使用 SSO；不挂载原生 API Key、供应商部署或 MCP 配置。 */
export default function PlatformAccessMenu() {
  const actions = useAuthStore((state) => state.platformWorkflowActions);
  const owner = useAuthStore((state) => state.userData?.id);
  const flow = useFlowsManagerStore((state) => state.currentFlow);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [output, setOutput] = useState("");
  const [error, setError] = useState("");
  const generation = useRef(0);
  const [published, setPublished] = useState(flow?.access_type === "PUBLIC");
  useEffect(() => {
    generation.current += 1;
    setOutput("");
    setError("");
    setBusy(false);
    setPublished(flow?.access_type === "PUBLIC");
  }, [owner, flow?.id, flow?.access_type, actions]);
  useEffect(
    () => () => {
      generation.current += 1;
    },
    [],
  );

  const perform = async (action: "publish" | "share" | "api_access") => {
    if (!flow || flow.user_id !== owner || !actions?.includes(action) || busy)
      return;
    const snapshot = generation.current;
    setBusy(true);
    setError("");
    setOutput("");
    try {
      const base = `${getURL("PLATFORM_WORKFLOW", {}, true)}/flows/${encodeURIComponent(flow.id)}`;
      const response =
        action === "publish"
          ? await api.post(`${base}/publish`, { published: !published })
          : await api.get(
              `${base}/${action === "api_access" ? "api" : "share"}`,
            );
      if (
        snapshot !== generation.current ||
        useAuthStore.getState().userData?.id !== owner ||
        !useAuthStore.getState().platformWorkflowActions?.includes(action)
      )
        return;
      if (action === "publish") {
        setPublished(response.data.access_type === "PUBLIC");
        setOutput(
          response.data.access_type === "PUBLIC"
            ? "已设置为发布状态；平台 SSO 和资源访问检查继续生效。"
            : "已取消发布。",
        );
      } else
        setOutput(
          action === "share"
            ? response.data.url
            : JSON.stringify(response.data, null, 2),
        );
    } catch (failure) {
      if (snapshot === generation.current)
        setError(extractApiErrorMessages(failure).join("；"));
    } finally {
      if (snapshot === generation.current) setBusy(false);
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
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>发布与访问</DialogTitle>
            <DialogDescription>
              发布标识和链接保留平台登录及资源隔离。API 使用有效工作流 Cookie
              和保存版本，不生成原生 API Key。外部云部署需要单独配置。
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-wrap gap-2">
            {actions.includes("publish") && (
              <Button disabled={busy} onClick={() => perform("publish")}>
                {published ? "取消发布" : "设置为发布状态"}
              </Button>
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
          {error && <p role="alert">{error}</p>}
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
