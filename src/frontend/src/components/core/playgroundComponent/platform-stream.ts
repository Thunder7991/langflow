import { getURL } from "@/controllers/API/helpers/constants";
import { handlePlatformSsoError } from "@/customization/utils/platform-sso-error";
import { syncPlatformWorkflowActions } from "@/customization/utils/platform-workflow-actions";
import useAuthStore from "@/stores/authStore";

export type PlaygroundEvent = {
  type:
    | "run_started"
    | "node_started"
    | "node_finished"
    | "run_finished"
    | "run_error"
    | "run_cancelled";
  run_id: string;
  version_token?: string;
  node_id?: string;
  duration_ms?: number;
  publication_id?: string;
  publication_version?: number;
  outputs?: { node_id: string; text: string }[];
  status_code?: number;
  detail?: string;
  code?: string;
  hostOrigin?: string;
};

export type PlaygroundRequest = {
  flowId: string;
  version: string;
  mode: "draft" | "published";
  input?: string;
  runId: string;
  signal: AbortSignal;
};

function checkSession(event: PlaygroundEvent) {
  if (event.status_code === 401)
    useAuthStore.getState().setPlatformWorkflowActions([]);
  handlePlatformSsoError(event);
}

/** SSE 只接收节点进度和最终文本; 断线结果未知时不自动重试。 */
export async function streamPlatformPlayground(
  options: PlaygroundRequest,
  receive: (event: PlaygroundEvent) => void,
) {
  const response = await fetch(
    `${getURL("PLATFORM_PLAYGROUND", {}, true)}/${encodeURIComponent(options.flowId)}`,
    {
      method: "POST",
      credentials: "include",
      signal: options.signal,
      headers: {
        "Content-Type": "application/json",
        "If-Match": options.version,
      },
      body: JSON.stringify({
        mode: options.mode,
        input: options.input,
        run_id: options.runId,
      }),
    },
  );
  syncPlatformWorkflowActions(response.headers.get("x-workflow-actions"));
  if (!response.ok) {
    const failure = await response.json();
    checkSession({ ...failure, status_code: response.status });
    const detail = failure.detail;
    throw new Error(
      typeof detail === "string"
        ? detail
        : (detail?.message ?? "运行未被受理，请核对版本和权限。"),
    );
  }
  if (!response.body) throw new Error("服务器未返回执行事件。");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let pending = "";
  let started = false;
  let finished = false;
  try {
    while (!finished) {
      const { value, done } = await reader.read();
      if (options.signal.aborted) return;
      pending += decoder.decode(value, { stream: !done });
      // 保留分片边界, 包括被拆开的 CRLF 与 UTF-8 字符。
      while (true) {
        const boundary = /\r?\n\r?\n/.exec(pending);
        if (!boundary) break;
        const frame = pending.slice(0, boundary.index);
        pending = pending.slice(boundary.index + boundary[0].length);
        const data = frame
          .split(/\r?\n/)
          .filter((line) => line.startsWith("data: "))
          .map((line) => line.slice(6))
          .join("\n");
        if (!data) continue;
        const event: PlaygroundEvent = JSON.parse(data);
        if (
          ![
            "run_started",
            "node_started",
            "node_finished",
            "run_finished",
            "run_error",
            "run_cancelled",
          ].includes(event.type)
        )
          throw new Error("执行事件格式不正确。");
        if (event.run_id !== options.runId)
          throw new Error("服务器运行标识不匹配。");
        if (event.type === "run_started") {
          if (started || event.version_token !== options.version)
            throw new Error("运行版本不匹配。");
          started = true;
        } else if (!started) throw new Error("缺少运行受理事件。");
        if (
          event.type === "run_finished" &&
          (event.version_token !== options.version ||
            (options.mode === "published" &&
              event.publication_id !== options.version))
        )
          throw new Error("运行版本不匹配。");
        if (
          event.type === "run_finished" &&
          (!Array.isArray(event.outputs) ||
            !event.outputs.length ||
            event.outputs.some(
              (output) =>
                typeof output.node_id !== "string" ||
                typeof output.text !== "string",
            ))
        )
          throw new Error("服务器未返回完整文本输出。");
        receive(event);
        checkSession(event);
        finished = ["run_finished", "run_error", "run_cancelled"].includes(
          event.type,
        );
        if (finished) break;
      }
      if (pending.length > 512 * 1024)
        throw new Error("执行事件超过允许大小。");
      if (done) break;
    }
    if (!finished && !options.signal.aborted)
      throw new Error("连接已中断，本次结果未知，请核对后再运行。");
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
