import { useTranslation } from "react-i18next";
import ForwardedIconComponent from "@/components/common/genericIconComponent";
import { usePermissions } from "@/contexts/permissionsContext";
import { PLATFORM_UNAVAILABLE_REASON } from "@/customization/utils/platform-workflow-ui";
import DeploymentStepperModal from "@/pages/MainPage/pages/deploymentsPage/components/deployment-stepper-modal";
import { useNavigateToTest } from "@/pages/MainPage/pages/deploymentsPage/hooks/use-navigate-to-test";
import useAuthStore from "@/stores/authStore";
import { useUtilityStore } from "@/stores/utilityStore";
import DeployChoiceDialog from "./deploy-choice-dialog";
import { usePrepareDeploy } from "./deploy-choice-dialog/hooks/use-prepare-deploy";

function DeployButtonInner() {
  const { t } = useTranslation();
  const {
    currentFlowId,
    isPreparingDeploy,
    choiceDialogOpen,
    setChoiceDialogOpen,
    deployModalOpen,
    setDeployModalOpen,
    providers,
    pendingSnapshotVersionId,
    initialVersionByFlow,
    stepperInitialProvider,
    stepperInitialInstance,
    handleDeploy,
    handleChooseNew,
    handleUpdateComplete,
    resetChoiceState,
  } = usePrepareDeploy();

  const navigateToTest = useNavigateToTest();

  const { can } = usePermissions();
  // Deploying provisions and runs the flow on an environment → gate on execute.
  const canDeploy = can(currentFlowId, "execute");

  return (
    <>
      <button
        type="button"
        onClick={handleDeploy}
        disabled={
          isPreparingDeploy ||
          choiceDialogOpen ||
          deployModalOpen ||
          !currentFlowId ||
          !canDeploy
        }
        className="relative inline-flex h-8 items-center justify-start gap-1.5 rounded bg-primary px-2 text-sm font-normal text-primary-foreground hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
        data-testid="deploy-btn-flow"
      >
        <ForwardedIconComponent
          name="Rocket"
          className={`h-4 w-4 ${isPreparingDeploy ? "animate-pulse" : ""}`}
        />
        <span className="font-normal text-mmd">{t("misc.deploy")}</span>
      </button>
      <DeployChoiceDialog
        open={choiceDialogOpen}
        setOpen={setChoiceDialogOpen}
        providers={providers}
        flowId={currentFlowId ?? ""}
        snapshotVersionId={pendingSnapshotVersionId}
        snapshotVersionTag={
          Array.from(initialVersionByFlow.values()).find(
            (entry) => entry.flowId === currentFlowId,
          )?.versionTag ?? ""
        }
        onChooseNew={handleChooseNew}
        onUpdateComplete={handleUpdateComplete}
        onTestDeployment={(deployment, providerId) => {
          resetChoiceState();
          navigateToTest(deployment, providerId);
        }}
      />
      <DeploymentStepperModal
        open={deployModalOpen}
        setOpen={setDeployModalOpen}
        initialFlowId={currentFlowId}
        initialVersionByFlow={initialVersionByFlow}
        initialProvider={stepperInitialProvider}
        initialInstance={stepperInitialInstance}
        onTestDeployment={(deployment, providerId) => {
          setDeployModalOpen(false);
          navigateToTest(deployment, providerId);
        }}
      />
    </>
  );
}

export default function DeployButton() {
  const isPlatform = useAuthStore(
    (state) => state.platformWorkflowActions != null,
  );
  const deploymentsEnabled = useUtilityStore(
    (s) => s.featureFlags.wxo_deployments === true,
  );
  if (!deploymentsEnabled) return null;
  if (isPlatform)
    return (
      <span title={PLATFORM_UNAVAILABLE_REASON}>
        <button
          type="button"
          disabled
          data-testid="deploy-btn-flow"
          className="px-2 text-muted-foreground"
        >
          部署（暂未开放）
        </button>
      </span>
    );
  return <DeployButtonInner />;
}
