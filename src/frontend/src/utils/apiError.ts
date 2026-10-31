/**
 * Shape produced by FastAPI validation errors and generic axios responses.
 * Kept internal — callers receive plain strings.
 */
type ApiDetailEntry = { msg?: string } | string;

/** Structured single-object detail, e.g. the pre-creation denial contract. */
type ApiDetailObject = { message?: unknown; [key: string]: unknown };

type ApiErrorShape = {
  platformWorkflow?: boolean;
  response?: {
    status?: number;
    headers?: Record<string, unknown>;
    data?: {
      code?: string;
      detail?: ApiDetailEntry[] | string | ApiDetailObject;
    };
  };
  message?: string;
};

/**
 * Extracts one or more human-readable messages from an unknown API error.
 *
 * Handles four FastAPI / axios error shapes in priority order:
 *   1. `response.data.detail` — array of `{ msg }` objects (validation errors)
 *   2. `response.data.detail` — plain string
 *   3. `response.data.detail` — object carrying a `message` (structured errors
 *      such as the pre-creation denial contract, which also carries
 *      `error_code` and limit fields the UI may read separately)
 *   4. `error.message`        — axios / native Error message
 *
 * Always returns at least one element so callers can spread directly into
 * `setErrorData({ list: extractApiErrorMessages(error) })`.
 */
export function extractApiErrorMessages(error: unknown): string[] {
  if (!error || typeof error !== "object") {
    return ["An unknown error occurred"];
  }

  const e = error as ApiErrorShape;
  const detail = e.response?.data?.detail;
  const code = e.response?.data?.code;
  const platformError =
    e.platformWorkflow ||
    code === "PLATFORM_SSO_REQUIRED" ||
    code === "PLATFORM_SSO_ERROR" ||
    e.response?.headers?.["x-workflow-actions"] !== undefined;
  if (
    e.response?.status === 409 &&
    typeof detail === "object" &&
    !Array.isArray(detail) &&
    detail?.code === "flow_version_conflict"
  ) {
    return ["流程版本发生冲突，当前草稿已保留，请比较后再加载最新版本。"];
  }
  if (platformError) {
    if (!e.response)
      return [
        "工作流网络连接异常，操作结果尚未确认；当前未保存内容仍保留，请恢复连接后核对。",
      ];
    if (e.response?.status === 401)
      return [
        "平台登录已失效，请返回平台重新登录；当前未保存内容仍保留在页面中。",
      ];
    if (e.response?.status === 403)
      return [
        "当前没有此操作的权限，或此功能尚未向平台开放；当前未保存内容仍保留。",
      ];
    if ((e.response?.status ?? 0) >= 500)
      return [
        "工作流或平台认证服务暂时不可用，请稍后重试；当前未保存内容仍保留。",
      ];
  }

  if (Array.isArray(detail)) {
    const msgs = detail
      .map((entry) => {
        if (typeof entry === "string") return entry;
        if (entry && typeof entry === "object") {
          const msg = (entry as { msg?: string }).msg;
          return typeof msg === "string" ? msg : JSON.stringify(entry);
        }
        return String(entry);
      })
      .filter(Boolean);
    if (msgs.length > 0) return msgs;
  }

  if (typeof detail === "string" && detail) return [detail];

  // A structured detail object: surface its human sentence rather than falling
  // through to the generic axios "Request failed with status code 403".
  if (detail && typeof detail === "object") {
    const message = (detail as ApiDetailObject).message;
    if (typeof message === "string" && message) return [message];
  }

  if (typeof e.message === "string" && e.message) return [e.message];
  return ["An unknown error occurred"];
}
