import { Outlet } from "react-router-dom";
import FlowMenu from "@/components/core/appHeaderComponent/components/FlowMenu";
import { PermissionsProvider } from "@/contexts/permissionsContext";
import useTheme from "@/customization/hooks/use-custom-theme";
import useFlowStore from "@/stores/flowStore";

export function DashboardWrapperPage() {
  useTheme();
  const currentFlow = useFlowStore((state) => state.currentFlow);
  const onFlowPage = useFlowStore((state) => state.onFlowPage);

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
