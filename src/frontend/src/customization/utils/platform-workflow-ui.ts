/** 仅约束平台嵌入模式的界面；最终授权和可信源码检查仍由服务端执行。 */
export const PLATFORM_UNAVAILABLE_REASON =
  "工作流操作按当前 IAM 权限开放；模型使用仍需 Secret 授权，原生供应商、Embedding、MCP、自定义代码和文件解析需单独适配。";
export const PLATFORM_READ_ONLY_REASON =
  "当前没有修改权限，未保存内容仍保留在当前页面。";

// 对应已审查基础节点和平台模型表单；执行白名单仍由服务端独立检查。
const EDITABLE_NODE_TYPES = new Set([
  "ChatInput",
  "ChatOutput",
  "TextInput",
  "TextOutput",
  "Prompt Template",
  "ParserComponent",
  "PlatformModel",
]);
export function isPlatformNodeAvailable(type: string): boolean {
  return EDITABLE_NODE_TYPES.has(type);
}

export function isPlatformWorkflowRouteAvailable(
  pathname: string,
  actions: string[] = [],
): boolean {
  if (/^\/assets\/files\/?$/.test(pathname))
    return actions.includes("file_read");
  if (/^\/(assets|components|mcp)(\/|$)/.test(pathname)) return false;
  if (/^\/settings(\/|$)/.test(pathname)) {
    return (
      /^\/settings\/?$/.test(pathname) ||
      /^\/settings\/general(\/|$)/.test(pathname) ||
      /^\/settings\/shortcuts\/?$/.test(pathname)
    );
  }
  return true;
}
