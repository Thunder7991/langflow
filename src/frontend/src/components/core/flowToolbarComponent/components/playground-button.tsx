import { useTranslation } from "react-i18next";
import ForwardedIconComponent from "@/components/common/genericIconComponent";
import ShadTooltip from "@/components/common/shadTooltipComponent";
import { SimpleSidebarTrigger } from "@/components/ui/simple-sidebar";
import { usePermissions } from "@/contexts/permissionsContext";
import { PLATFORM_UNAVAILABLE_REASON } from "@/customization/utils/platform-workflow-ui";
import useAuthStore from "@/stores/authStore";
import useFlowsManagerStore from "@/stores/flowsManagerStore";
import PlatformDryRunButton from "./platform-dry-run-button";

interface PlaygroundButtonProps {
  hasIO: boolean;
}

const ButtonLabel = () => {
  const { t } = useTranslation();
  return <span className="font-normal text-mmd">{t("misc.playground")}</span>;
};

const DisabledButton = () => (
  <button
    type="button"
    disabled
    className="relative inline-flex h-8 w-auto items-center justify-start gap-1.5 rounded px-2 text-sm font-normal cursor-not-allowed text-muted-foreground"
    data-testid="playground-btn-flow"
  >
    <ForwardedIconComponent name="Play" className="h-4 w-4" />
    <ButtonLabel />
  </button>
);

const PlaygroundButton = ({ hasIO }: PlaygroundButtonProps) => {
  const { t } = useTranslation();
  const { can } = usePermissions();
  const isPlatform = useAuthStore(
    (state) => state.platformWorkflowActions != null,
  );
  const currentFlowId = useFlowsManagerStore((state) => state.currentFlowId);
  // Running a flow in the playground executes it → gate on the execute action.
  const canRun = can(currentFlowId, "execute");

  if (isPlatform)
    return (
      <>
        <PlatformDryRunButton />
        <PlatformDryRunButton action="run" />
      </>
    );

  if (!canRun) {
    return (
      <ShadTooltip
        content={
          isPlatform
            ? PLATFORM_UNAVAILABLE_REASON
            : t("sidebar.permissionDenied")
        }
      >
        <div>
          <DisabledButton />
        </div>
      </ShadTooltip>
    );
  }

  return hasIO ? (
    <SimpleSidebarTrigger>
      <ButtonLabel />
    </SimpleSidebarTrigger>
  ) : (
    <ShadTooltip content={t("misc.addChatInputOutputPlayground")}>
      <div>
        <DisabledButton />
      </div>
    </ShadTooltip>
  );
};

export default PlaygroundButton;
