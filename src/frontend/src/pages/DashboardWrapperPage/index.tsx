import { useEffect } from "react";
import { Outlet } from "react-router-dom";
import FlowMenu from "@/components/core/appHeaderComponent/components/FlowMenu";
import { PermissionsProvider } from "@/contexts/permissionsContext";
import useTheme from "@/customization/hooks/use-custom-theme";
import useFlowStore from "@/stores/flowStore";

export function DashboardWrapperPage() {
  useTheme();
  const currentFlow = useFlowStore((state) => state.currentFlow);
  const onFlowPage = useFlowStore((state) => state.onFlowPage);

  useEffect(() => {
    if (window.parent === window) return;
    let paintFrame = 0;
    const handleMessage = (event: MessageEvent) => {
      if (
        event.source !== window.parent ||
        !/^https?:\/\//.test(event.origin) ||
        event.data?.channel !== "workflow-ui" ||
        event.data.status !== "check-ready" ||
        typeof event.data.requestId !== "string"
      )
        return;
      cancelAnimationFrame(paintFrame);
      // 这里只报告布局可展示；身份认证仍由 SSO 桥接负责。两帧之间留出主题和布局绘制机会。
      paintFrame = requestAnimationFrame(() => {
        paintFrame = requestAnimationFrame(() => {
          window.parent.postMessage(
            {
              channel: "workflow-ui",
              status: "ready",
              requestId: event.data.requestId,
            },
            event.origin,
          );
        });
      });
    };
    window.addEventListener("message", handleMessage);
    return () => {
      window.removeEventListener("message", handleMessage);
      cancelAnimationFrame(paintFrame);
    };
  }, []);

  return (
    <PermissionsProvider
      resourceType="flow"
      resourceIds={currentFlow?.id ? [currentFlow.id] : []}
      domain={
        currentFlow?.folder_id ? `project:${currentFlow.folder_id}` : undefined
      }
    >
      <div className="relative flex h-screen w-full flex-col overflow-hidden">
        {/* FlowMenu 也负责流程列表初始化，须常驻；仅在画布页展示其流程操作浮层。 */}
        <div
          className={
            onFlowPage
              ? "absolute left-1/2 top-2 z-20 max-w-[70%] -translate-x-1/2 rounded-lg border bg-background px-3 py-2 shadow-sm"
              : "contents"
          }
        >
          <FlowMenu />
        </div>
        <div className="flex min-h-0 w-full flex-1 flex-row overflow-hidden">
          <Outlet />
        </div>
      </div>
    </PermissionsProvider>
  );
}
