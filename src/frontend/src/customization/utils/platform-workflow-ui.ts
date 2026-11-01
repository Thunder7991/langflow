/** 仅约束平台嵌入模式的界面；最终授权和可信源码检查仍由服务端执行。 */
export const PLATFORM_UNAVAILABLE_REASON =
  "当前平台按权限开放本人普通流程、个人文件、纯文本及平台模型受控调试；导入导出、复制流程、删除、通用运行、供应商原生节点、发布及 MCP 管理尚未开放。";
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
