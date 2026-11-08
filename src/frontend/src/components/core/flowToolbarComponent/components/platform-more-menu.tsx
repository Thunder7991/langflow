import { useRef, useState } from "react";
import ForwardedIconComponent from "@/components/common/genericIconComponent";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import useAuthStore from "@/stores/authStore";
import useFlowStore from "@/stores/flowStore";
import useFlowsManagerStore from "@/stores/flowsManagerStore";
import PlatformDryRunButton from "./platform-dry-run-button";

export default function PlatformMoreMenu() {
  const actions = useAuthStore((state) => state.platformWorkflowActions);
  const owner = useAuthStore((state) => state.userData?.id);
  const current = useFlowStore((state) => state.currentFlow);
  const usesModel = useFlowStore((state) =>
    state.nodes.some((node) => node.data?.type === "PlatformModel"),
  );
  const saved = useFlowsManagerStore((state) => state.currentFlow);
  const [activeAction, setActiveAction] = useState<"dry_run" | "run" | null>(
    null,
  );
  const moreButton = useRef<HTMLButtonElement>(null);
  if (actions == null) return null;

  const canDebug = actions.includes("read") && actions.includes("dry_run");
  const canRun = actions.includes("read") && actions.includes("run");
  const sameFlow =
    !!owner &&
    !!current &&
    current.id === saved?.id &&
    saved?.user_id === owner;

  const restoreFocus = (event: Event) => {
    if (moreButton.current) {
      event.preventDefault();
      moreButton.current.focus();
    }
  };

  return (
    <>
      {(canDebug || canRun) && (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              ref={moreButton}
              size="sm"
              variant="ghost"
              data-testid="platform-more-menu-button"
            >
              更多
              <ForwardedIconComponent
                name="ChevronDown"
                className="h-3.5 w-3.5"
              />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent
            align="end"
            onCloseAutoFocus={(event) => {
              if (activeAction) event.preventDefault();
            }}
          >
            {canDebug && (
              <DropdownMenuItem
                disabled={!sameFlow || !saved?.version_token}
                onSelect={() => setActiveAction("dry_run")}
              >
                {usesModel ? "平台模型调试" : "纯文本调试"}
              </DropdownMenuItem>
            )}
            {canRun && (
              <DropdownMenuItem
                disabled={!sameFlow}
                onSelect={() => setActiveAction("run")}
              >
                工作流运行
              </DropdownMenuItem>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      )}
      {/* 弹窗常驻菜单外，收起菜单不会卸载执行等待、停止操作及运行结果。 */}
      <PlatformDryRunButton
        showTrigger={false}
        open={activeAction === "dry_run"}
        onOpenChange={(open) => setActiveAction(open ? "dry_run" : null)}
        onCloseAutoFocus={restoreFocus}
      />
      <PlatformDryRunButton
        action="run"
        showTrigger={false}
        open={activeAction === "run"}
        onOpenChange={(open) => setActiveAction(open ? "run" : null)}
        onCloseAutoFocus={restoreFocus}
      />
    </>
  );
}
