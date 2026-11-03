import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  deletePersonalFile,
  type PersonalFile,
  readPersonalFile,
  renamePersonalFile,
  uploadPersonalFile,
  usePlatformFilePolicy,
  usePlatformFiles,
} from "@/controllers/API/queries/platform-files/personal-files";
import DeleteConfirmationModal from "@/modals/deleteConfirmationModal";
import useAuthStore from "@/stores/authStore";
import { extractApiErrorMessages } from "@/utils/apiError";

/** 平台个人文件只挂载受限接口，不复用含重命名、删除、共享和批量请求的原生管理器。 */
export default function PlatformFilesPage() {
  const owner = useAuthStore((state) => state.userData?.id);
  const actions = useAuthStore((state) => state.platformWorkflowActions);
  const canRead = actions?.includes("file_read") ?? false;
  const canUpload = canRead && (actions?.includes("file_upload") ?? false);
  const canDownload = canRead && (actions?.includes("file_download") ?? false);
  const canRename = canRead && (actions?.includes("file_update") ?? false);
  const canDelete = canRead && (actions?.includes("file_delete") ?? false);
  const [editing, setEditing] = useState<string | null>(null);
  const [name, setName] = useState("");
  const files = usePlatformFiles();
  const policy = usePlatformFilePolicy();
  const [selected, setSelected] = useState<File | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [progress, setProgress] = useState(0);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [preview, setPreview] = useState<{ name: string; text: string } | null>(
    null,
  );
  const upload = useRef<AbortController | null>(null);
  const read = useRef<AbortController | null>(null);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!canUpload) upload.current?.abort();
    if (!canRead || !canDownload) read.current?.abort();
  }, [canRead, canUpload, canDownload]);
  useEffect(
    () => () => {
      upload.current?.abort();
      read.current?.abort();
    },
    [owner],
  );

  const refresh = () => {
    if (
      useAuthStore.getState().userData?.id === owner &&
      useAuthStore.getState().platformWorkflowActions?.includes("file_read")
    )
      void files.refetch();
  };

  const modify = async (file: PersonalFile, remove = false) => {
    if (!owner || busy || !(remove ? canDelete : canRename)) return;
    setBusy(remove ? "delete" : "rename");
    setError("");
    setMessage("");
    try {
      if (remove) await deletePersonalFile(owner, file.id);
      else await renamePersonalFile(owner, file.id, name);
      if (useAuthStore.getState().userData?.id !== owner) return;
      setEditing(null);
      setPreview(null);
      setMessage(remove ? "文件已删除。" : "文件已重命名。");
      refresh();
    } catch (failure) {
      if (useAuthStore.getState().userData?.id === owner)
        setError(extractApiErrorMessages(failure).join("；"));
    } finally {
      if (useAuthStore.getState().userData?.id === owner) setBusy(null);
    }
  };

  const handleUpload = async () => {
    if (!owner || !selected || !canUpload || busy || !policy.data) return;
    setError("");
    setMessage("");
    const extension = selected.name
      .slice(selected.name.lastIndexOf("."))
      .toLowerCase();
    if (
      selected.size > policy.data.max_bytes ||
      !policy.data.extensions.includes(extension)
    ) {
      setError("文件大小或类型不符合上传限制。");
      return;
    }
    const controller = new AbortController();
    upload.current = controller;
    setBusy("upload");
    setProgress(0);
    try {
      await uploadPersonalFile(owner, selected, controller.signal, setProgress);
      if (controller.signal.aborted) return;
      setMessage("上传成功");
      setSelected(null);
      if (input.current) input.current.value = "";
    } catch (failure) {
      setError(
        controller.signal.aborted
          ? "上传已取消或权限已变化；请刷新列表核对是否已完成。"
          : extractApiErrorMessages(failure).join("；"),
      );
    } finally {
      if (upload.current === controller) {
        upload.current = null;
        setBusy(null);
        refresh();
      }
    }
  };

  const handleRead = async (file: PersonalFile, isPreview: boolean) => {
    if (!owner || !canRead || (!isPreview && !canDownload) || busy) return;
    const controller = new AbortController();
    read.current = controller;
    setBusy(file.id);
    setError("");
    setMessage("");
    setPreview(null);
    try {
      if (isPreview) {
        const text = await readPersonalFile(
          owner,
          file.id,
          true,
          controller.signal,
        );
        if (!controller.signal.aborted) setPreview({ name: file.name, text });
      } else {
        const blob = await readPersonalFile(
          owner,
          file.id,
          false,
          controller.signal,
        );
        if (controller.signal.aborted) return;
        const objectUrl = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = objectUrl;
        anchor.download = file.name;
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        // 给浏览器消费下载地址的时间；不把触发下载当作已写入本机磁盘。
        window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
      }
    } catch (failure) {
      if (!controller.signal.aborted)
        setError(extractApiErrorMessages(failure).join("；"));
    } finally {
      if (read.current === controller) {
        read.current = null;
        setBusy(null);
      }
    }
  };

  if (!canRead)
    return (
      <p className="p-6" role="status">
        需要平台个人文件读取权限，已有文件未改变。
      </p>
    );
  return (
    <section className="flex h-full w-full flex-col gap-5 overflow-auto p-6">
      <h1 className="text-xl font-semibold">我的文件</h1>
      <p className="text-sm text-muted-foreground">
        仅显示本人文件，各项操作按平台权限开放。被流程或历史版本引用的文件需先解除引用，才能重命名或删除。
      </p>
      {policy.data && (
        <p className="text-sm text-muted-foreground">
          单文件上限 {policy.data.max_bytes} 字节；支持{" "}
          {policy.data.extensions.join("、")}。文本预览最多{" "}
          {policy.data.preview_bytes} 字节。
        </p>
      )}
      {policy.error && (
        <p role="alert">{extractApiErrorMessages(policy.error).join("；")}</p>
      )}
      <div className="flex flex-wrap items-center gap-3">
        {canUpload && (
          <>
            <label className="text-sm">
              选择文件
              <input
                ref={input}
                aria-label="选择文件"
                type="file"
                className="ml-2 max-w-full"
                accept={policy.data?.extensions.join(",")}
                disabled={!canUpload || !!busy || !policy.data}
                onChange={(event) => {
                  setSelected(event.target.files?.[0] ?? null);
                  setMessage("");
                  setError("");
                }}
              />
            </label>
            <Button
              size="sm"
              disabled={!canUpload || !selected || !!busy || !policy.data}
              onClick={handleUpload}
            >
              上传
            </Button>
          </>
        )}
        {busy === "upload" && (
          <>
            <span role="status">
              {progress < 100 ? `上传中 ${progress}%` : "服务端校验中"}
            </span>
            <Button
              size="sm"
              variant="outline"
              onClick={() => upload.current?.abort()}
            >
              取消上传
            </Button>
          </>
        )}
        <Button
          size="sm"
          variant="outline"
          disabled={!!busy || files.isFetching}
          onClick={() => {
            refresh();
            void policy.refetch();
          }}
        >
          刷新列表
        </Button>
      </div>
      {!canUpload && (
        <p className="text-sm" role="status">
          当前没有上传权限。
        </p>
      )}
      {!canDownload && (
        <p className="text-sm" role="status">
          当前没有下载权限，可预览符合限制的文本。
        </p>
      )}
      {error && <p role="alert">{error}</p>}
      {message && <p role="status">{message}</p>}
      {files.isPending ? (
        <p role="status">正在加载文件…</p>
      ) : files.error ? (
        <p role="alert">{extractApiErrorMessages(files.error).join("；")}</p>
      ) : (
        <>
          {files.data?.length === 0 && <p role="status">暂无个人文件。</p>}
          <ul className="divide-y">
            {files.data?.map((file) => (
              <li
                key={file.id}
                className="flex flex-wrap items-center gap-3 py-3"
              >
                <span className="min-w-0 flex-1 break-all">{file.name}</span>
                {canRename && editing === file.id && (
                  <>
                    <input
                      aria-label={`新文件名 ${file.name}`}
                      className="rounded border bg-background p-2 text-sm"
                      value={name}
                      onChange={(event) => setName(event.target.value)}
                    />
                    <Button
                      size="sm"
                      disabled={!!busy || !name.trim()}
                      onClick={() => modify(file)}
                    >
                      保存名称
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={!!busy}
                      onClick={() => setEditing(null)}
                    >
                      取消
                    </Button>
                  </>
                )}
                <span className="text-sm text-muted-foreground">
                  {file.size} 字节
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  aria-label={`预览 ${file.name}`}
                  disabled={
                    !!busy ||
                    !policy.data ||
                    file.size > policy.data.preview_bytes ||
                    !/\.(txt|md|csv|json)$/i.test(file.name)
                  }
                  onClick={() => handleRead(file, true)}
                >
                  预览
                </Button>
                {canDownload && (
                  <Button
                    variant="outline"
                    size="sm"
                    aria-label={`下载 ${file.name}`}
                    disabled={!canDownload || !!busy}
                    onClick={() => handleRead(file, false)}
                  >
                    下载
                  </Button>
                )}
                {canRename && editing !== file.id && (
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={!!busy}
                    onClick={() => {
                      setEditing(file.id);
                      setName(file.name);
                    }}
                  >
                    重命名
                  </Button>
                )}
                {canDelete && (
                  <DeleteConfirmationModal
                    asChild
                    description={file.name}
                    onConfirm={() => modify(file, true)}
                  >
                    <Button variant="destructive" size="sm" disabled={!!busy}>
                      删除
                    </Button>
                  </DeleteConfirmationModal>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
      {preview && (
        <div className="min-h-0 rounded-md border p-4">
          <div className="mb-3 flex items-center justify-between gap-2">
            <h2 className="break-all font-medium">{preview.name}</h2>
            <Button variant="ghost" size="sm" onClick={() => setPreview(null)}>
              关闭预览
            </Button>
          </div>
          <pre className="overflow-auto whitespace-pre-wrap break-words text-sm">
            {preview.text}
          </pre>
        </div>
      )}
    </section>
  );
}
