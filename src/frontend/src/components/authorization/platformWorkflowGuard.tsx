import type { ReactNode } from "react";
import { useLocation, useParams } from "react-router-dom";
import { CustomLink } from "@/customization/components/custom-link";
import {
  isPlatformWorkflowRouteAvailable,
  PLATFORM_UNAVAILABLE_REASON,
} from "@/customization/utils/platform-workflow-ui";
import useAuthStore from "@/stores/authStore";

/** 深链接同样拦截，不挂载未开放页面，避免子页面自动发起管理请求。 */
export function PlatformWorkflowGuard({ children }: { children: ReactNode }) {
  const platformActions = useAuthStore(
    (state) => state.platformWorkflowActions,
  );
  const { pathname } = useLocation();
  const { customParam } = useParams();
  const path = customParam ? pathname.slice(customParam.length + 1) : pathname;
  if (
    platformActions != null &&
    !isPlatformWorkflowRouteAvailable(path, platformActions)
  ) {
    return (
      <div
        className="flex h-full flex-col items-center justify-center gap-4 p-6"
        role="status"
      >
        <p>{PLATFORM_UNAVAILABLE_REASON}</p>
        <CustomLink to="/flows" className="underline">
          返回工作流列表
        </CustomLink>
      </div>
    );
  }
  return <>{children}</>;
}
