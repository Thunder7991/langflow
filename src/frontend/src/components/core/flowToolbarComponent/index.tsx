import { Panel } from "@xyflow/react";
import { memo, useEffect, useState } from "react";
import { useHotkeys } from "react-hotkeys-hook";
import { track } from "@/customization/utils/analytics";
import ExportModal from "@/modals/exportModal";
import useAuthStore from "@/stores/authStore";
import { usePlaygroundStore } from "@/stores/playgroundStore";
import useFlowStore from "../../../stores/flowStore";
import { useShortcutsStore } from "../../../stores/shortcuts";
import { cn, isThereModal } from "../../../utils/utils";
import FlowToolbarOptions from "./components/flow-toolbar-options";

const FlowToolbar = memo(function FlowToolbar(): JSX.Element {
  const preventDefault = true;
  const actions = useAuthStore((state) => state.platformWorkflowActions);
  const isPlatform = actions != null;
  const [openApiModal, setOpenApiModal] = useState<boolean>(false);
  const [openExportModal, setOpenExportModal] = useState<boolean>(false);
  const isPlaygroundOpen = usePlaygroundStore((state) => state.isOpen);
  const setPlaygroundOpen = usePlaygroundStore((state) => state.setIsOpen);
  const handleAPIWShortcut = (e: KeyboardEvent) => {
    if (isPlatform) {
      if (actions.includes("api_access"))
        document
          .querySelector<HTMLButtonElement>(
            '[data-testid="platform-access-menu-button"]',
          )
          ?.click();
      return;
    }
    if (isThereModal() && !openApiModal) return;
    setOpenApiModal((oldOpen) => !oldOpen);
  };

  const handleChatWShortcut = (e: KeyboardEvent) => {
    if (isPlatform) {
      if (
        actions.includes("read") &&
        (actions.includes("dry_run") || actions.includes("run"))
      )
        setPlaygroundOpen(!isPlaygroundOpen);
      return;
    }
    if (isThereModal() && !isPlaygroundOpen) return;
    if (useFlowStore.getState().hasIO) {
      setPlaygroundOpen(!isPlaygroundOpen);
    }
  };

  const handleShareWShortcut = (e: KeyboardEvent) => {
    if (isPlatform && !actions.includes("export")) return;
    if (isThereModal() && !openExportModal) return;
    setOpenExportModal((oldState) => !oldState);
  };

  const openPlayground = useShortcutsStore((state) => state.openPlayground);
  const api = useShortcutsStore((state) => state.api);
  const flow = useShortcutsStore((state) => state.flow);

  useHotkeys(openPlayground, handleChatWShortcut, { preventDefault });
  useHotkeys(api, handleAPIWShortcut, { preventDefault });
  useHotkeys(flow, handleShareWShortcut, { preventDefault });

  useEffect(() => {
    // 能力刷新保留已开放的聊天面板; 完全撤销执行权限才收起。
    if (isPlatform) {
      if (
        !actions.includes("read") ||
        (!actions.includes("dry_run") && !actions.includes("run"))
      )
        setPlaygroundOpen(false);
      setOpenApiModal(false);
      if (!actions.includes("export")) setOpenExportModal(false);
    }
  }, [isPlatform, actions, setPlaygroundOpen]);

  useEffect(() => {
    if (isPlaygroundOpen) {
      track("Playground Button Clicked");
    }
  }, [isPlaygroundOpen]);

  return (
    <>
      <Panel className="!top-auto !m-2" position="top-right">
        <div
          className={cn(
            "hover:shadow-round-btn-shadow flex h-11 items-center justify-center gap-7 rounded-md border bg-background px-1.5 shadow transition-all",
          )}
        >
          <FlowToolbarOptions
            openApiModal={openApiModal}
            setOpenApiModal={setOpenApiModal}
          />
        </div>
      </Panel>
      {(!isPlatform || actions.includes("export")) && (
        <ExportModal open={openExportModal} setOpen={setOpenExportModal} />
      )}
    </>
  );
});

export default FlowToolbar;
