/** 平台 SSO 错误不走原生 refresh；明确失效才通知宿主，服务故障不触发重登。 */
export function handlePlatformSsoError(data: unknown): boolean {
  if (!data || typeof data !== "object") return false;
  const error = data as { code?: string; hostOrigin?: string };
  if (
    error.code !== "PLATFORM_SSO_REQUIRED" &&
    error.code !== "PLATFORM_SSO_ERROR"
  )
    return false;
  if (error.code === "PLATFORM_SSO_REQUIRED" && error.hostOrigin) {
    try {
      const host = new URL(error.hostOrigin);
      if (
        ["http:", "https:"].includes(host.protocol) &&
        host.origin === error.hostOrigin
      ) {
        window.parent.postMessage(
          { channel: "workflow-sso", status: "expired" },
          host.origin,
        );
      }
    } catch {
      // 非法来源不通知；绝不使用 * 广播登录状态。
    }
  }
  return true;
}
