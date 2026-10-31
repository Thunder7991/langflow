import type { ComponentProps } from "react";
import { useTranslation } from "react-i18next";
import type { handleOnNewValueType } from "@/CustomNodes/hooks/use-handle-new-value";
import { ParameterRenderComponent } from "@/components/core/parameterRenderComponent";
import type { NodeInfoType } from "@/components/core/parameterRenderComponent/types";
import { useIsFlowReadOnly } from "@/contexts/permissionsContext";
import useAuthStore from "@/stores/authStore";
import useFlowStore from "@/stores/flowStore";
import type { APIClassType, InputFieldType } from "@/types/api";
import type { targetHandleType } from "@/types/flow";
import { scapedJSONStringfy } from "@/utils/reactflowUtils";
import { cn } from "@/utils/utils";
import PlatformFileInput from "./platform-file-input";

export function CustomParameterComponent({
  handleOnNewValue,
  name,
  nodeId,
  inputId,
  templateData,
  templateValue,
  showParameter,
  inspectionPanel = false,
  editNode,
  handleNodeClass,
  nodeClass,
  placeholder,
  isToolMode = false,
  nodeInformationMetadata,
  proxy,
  ariaLabelledBy,
}: {
  handleOnNewValue: handleOnNewValueType;
  name: string;
  nodeId: string;
  inputId: targetHandleType;
  templateData: Partial<InputFieldType>;
  templateValue: unknown;
  showParameter: boolean;
  inspectionPanel: boolean;
  editNode: boolean;
  handleNodeClass: ComponentProps<
    typeof ParameterRenderComponent
  >["handleNodeClass"];
  nodeClass: APIClassType;
  placeholder?: string;
  isToolMode?: boolean;
  nodeInformationMetadata?: NodeInfoType;
  proxy: { field: string; id: string } | undefined;
  ariaLabelledBy?: string;
}) {
  const { t } = useTranslation();
  const edges = useFlowStore((state) => state.edges);
  const currentFlowId = useFlowStore((state) => state.currentFlow?.id);
  const isReadOnly = useIsFlowReadOnly(currentFlowId);
  const platformActions = useAuthStore(
    (state) => state.platformWorkflowActions,
  );
  const isPlatform = platformActions != null;

  const disabled =
    edges.some(
      (edge) =>
        edge.targetHandle ===
        scapedJSONStringfy(proxy ? { ...inputId, proxy } : inputId),
    ) || isToolMode;

  if (isPlatform && templateData.type === "file") {
    if (!platformActions.includes("file_read"))
      return (
        <p className="text-sm text-muted-foreground" role="status">
          需要平台个人文件读取权限；已有引用保留。
        </p>
      );
    return (
      <PlatformFileInput
        paths={templateData.file_path}
        value={templateValue}
        multiple={Boolean(templateData.list)}
        disabled={isReadOnly || disabled || Boolean(templateData.readonly)}
        onChange={handleOnNewValue}
      />
    );
  }
  // 凭据选择器本身含自动请求；不能只禁用外层，否则仍挂载未授权的管理接口。
  if (
    isPlatform &&
    ["mcp", "model", "knowledge_backend", "connect", "connection_ref"].includes(
      templateData.type ?? "",
    )
  ) {
    return (
      <p className="text-sm text-muted-foreground" role="status">
        模型、连接与 MCP 配置尚未开放；已有引用保留在流程中。
      </p>
    );
  }

  return (
    <fieldset
      data-testid="parameter-permission-gate"
      className={cn(
        "m-0 w-full min-w-0 border-0 p-0",
        isReadOnly && "pointer-events-none opacity-60",
      )}
      // 原生禁用后代控件, 不传入旧 disabled 属性, 避免连线字段清空逻辑抹掉草稿。
      disabled={isReadOnly}
      inert={isReadOnly}
      aria-disabled={isReadOnly}
      title={isReadOnly ? t("version.readOnly") : undefined}
    >
      <ParameterRenderComponent
        handleOnNewValue={(changes, options) => {
          // 已打开的 Portal 弹窗位于 fieldset 外, 收权后回调也不能更改画布。
          if (!isReadOnly) handleOnNewValue(changes, options);
        }}
        name={name}
        nodeId={nodeId}
        templateData={templateData}
        templateValue={templateValue}
        editNode={editNode}
        showParameter={showParameter}
        inspectionPanel={inspectionPanel}
        handleNodeClass={(value, code, type) => {
          if (!isReadOnly) handleNodeClass(value, code, type);
        }}
        nodeClass={nodeClass}
        disabled={disabled}
        placeholder={placeholder}
        isToolMode={isToolMode}
        nodeInformationMetadata={nodeInformationMetadata}
        ariaLabelledBy={ariaLabelledBy}
      />
    </fieldset>
  );
}

export function getCustomParameterTitle({
  title,
  isFlexView,
  required,
  inspectionPanel,
  labelId,
  requiredText,
}: {
  title: string;
  nodeId: string;
  isFlexView: boolean;
  required?: boolean;
  inspectionPanel?: boolean;
  labelId?: string;
  requiredText?: string;
}) {
  return (
    <div id={labelId} className={cn(isFlexView && "max-w-56 truncate")}>
      <span
        data-testid={`title-${title.toLocaleLowerCase()}`}
        className={cn(
          inspectionPanel
            ? "text-xs font-medium"
            : "text-sm text-secondary-foreground",
        )}
      >
        {title}
      </span>
      {required && (
        <>
          <span className="text-destructive" aria-hidden="true">
            *
          </span>
          <span className="sr-only">{requiredText}</span>
        </>
      )}
    </div>
  );
}

export function CustomParameterLabel({
  name,
  nodeId,
  templateValue,
  nodeClass,
}: {
  name: string;
  nodeId: string;
  templateValue: unknown;
  nodeClass: APIClassType;
}) {
  return <></>;
}
