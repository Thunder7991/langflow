import type { handleOnNewValueType } from "@/CustomNodes/hooks/use-handle-new-value";
import { usePlatformFiles } from "@/controllers/API/queries/platform-files/personal-files";
import { CustomLink } from "@/customization/components/custom-link";
import useAuthStore from "@/stores/authStore";
import { extractApiErrorMessages } from "@/utils/apiError";

export default function PlatformFileInput({
  paths,
  value,
  disabled,
  multiple,
  onChange,
}: {
  paths: unknown;
  value: unknown;
  disabled: boolean;
  multiple: boolean;
  onChange: handleOnNewValueType;
}) {
  const actions = useAuthStore((state) => state.platformWorkflowActions);
  const canRead = actions?.includes("file_read") ?? false;
  const editable =
    !disabled && canRead && (actions?.includes("update") ?? false);
  const files = usePlatformFiles();
  const selected: string[] = Array.isArray(paths) ? paths : [];
  const names: string[] = Array.isArray(value) ? value : [];
  const canChange = () => {
    const latest = useAuthStore.getState().platformWorkflowActions;
    return (
      editable && latest?.includes("file_read") && latest.includes("update")
    );
  };

  // 获取失败和收权只收起选择能力，不能清除已经保存在节点里的文件引用。
  if (!canRead)
    return (
      <p className="text-sm text-muted-foreground" role="status">
        需要平台个人文件读取权限；已有引用保留。
      </p>
    );
  return (
    <div className="flex w-full flex-col gap-2 text-sm">
      {names.length > 0 && <p className="break-all">{names.join("、")}</p>}
      {files.error && (
        <p role="alert">{extractApiErrorMessages(files.error).join("；")}</p>
      )}
      {files.isPending && <p role="status">正在加载个人文件…</p>}
      <select
        aria-label="选择个人文件"
        role="listbox"
        size={3}
        multiple={multiple}
        value={multiple ? selected : (selected[0] ?? "")}
        disabled={!editable || files.isPending || !!files.error}
        className="nodrag w-full rounded-md border bg-background p-2"
        onChange={(event) => {
          if (!canChange()) return;
          const chosen = Array.from(event.target.selectedOptions)
            .map((option) =>
              files.data?.find((file) => file.path === option.value),
            )
            .filter((file) => file != null);
          if (!chosen.length) return;
          onChange({
            value: chosen.map((file) => file.name),
            file_path: chosen.map((file) => file.path),
          });
        }}
      >
        <option value="" disabled>
          选择已上传的本人文件
        </option>
        {files.data?.map((file) => (
          <option key={file.id} value={file.path}>
            {file.name}
          </option>
        ))}
      </select>
      {selected.some(
        (path) => files.data && !files.data.some((file) => file.path === path),
      ) && (
        <p role="status">部分已有引用当前不可用，请重新选择或清除后再保存。</p>
      )}
      <div className="flex gap-3">
        <button
          type="button"
          className="underline disabled:opacity-50"
          disabled={!editable || selected.length === 0}
          onClick={() => {
            if (canChange()) onChange({ value: [], file_path: [] });
          }}
        >
          清除选择
        </button>
        <button
          type="button"
          className="underline"
          disabled={files.isFetching}
          onClick={() => void files.refetch()}
        >
          刷新文件
        </button>
        <CustomLink
          to="/assets/files"
          target="_blank"
          rel="noopener noreferrer"
          className="underline"
        >
          管理我的文件
        </CustomLink>
      </div>
    </div>
  );
}
