import {
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CloudUpload,
  Download,
  Eye,
  FileImage,
  FileJson,
  FileSpreadsheet,
  FileText,
  FolderOpen,
  Info,
  Loader2,
  Pencil,
  RefreshCw,
  Search,
  ShieldCheck,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  deletePersonalFile,
  type PersonalFile,
  readPersonalFile,
  renamePersonalFile,
  uploadPersonalFile,
  usePlatformFilePolicy,
  usePlatformFiles,
} from "@/controllers/API/queries/platform-files/personal-files";
import { useDocumentTitle } from "@/hooks/use-document-title";
import DeleteConfirmationModal from "@/modals/deleteConfirmationModal";
import useAuthStore from "@/stores/authStore";
import { extractApiErrorMessages } from "@/utils/apiError";
import { parseApiTimestamp } from "@/utils/dateTime";
import { formatFileSize } from "@/utils/stringManipulation";
import { cn } from "@/utils/utils";

const PAGE_SIZE = 10;
const FILE_FILTERS = [
  { value: "all", label: "全部文件", accessibleLabel: "全部文件" },
  { value: "document", label: "文档", accessibleLabel: "文档文件" },
  { value: "data", label: "数据", accessibleLabel: "数据文件" },
  { value: "image", label: "图片", accessibleLabel: "图片文件" },
] as const;
type FileFilter = (typeof FILE_FILTERS)[number]["value"];
type FileSort = "newest" | "oldest" | "name" | "size-desc" | "size-asc";

function extensionOf(name: string) {
  return name.includes(".")
    ? name.slice(name.lastIndexOf(".")).toLowerCase()
    : "";
}

function categoryOf(name: string): Exclude<FileFilter, "all"> {
  const extension = extensionOf(name);
  if ([".png", ".jpg", ".jpeg", ".webp"].includes(extension)) return "image";
  return [".csv", ".json"].includes(extension) ? "data" : "document";
}

function FileIcon({ name, className }: { name: string; className?: string }) {
  const extension = extensionOf(name);
  const Icon =
    categoryOf(name) === "image"
      ? FileImage
      : extension === ".csv"
        ? FileSpreadsheet
        : extension === ".json"
          ? FileJson
          : FileText;
  return (
    <span
      className={cn(
        "flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border bg-muted/40 text-muted-foreground",
        className,
      )}
    >
      <Icon className="h-5 w-5" strokeWidth={1.5} aria-hidden="true" />
    </span>
  );
}

function displayDate(value: string) {
  const date = parseApiTimestamp(value);
  return date
    ? new Intl.DateTimeFormat("zh-CN", {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      }).format(date)
    : "—";
}

/** 复用应用外壳和主题，文件请求仍统一走平台的本人文件接口。 */
export default function PlatformFilesPage() {
  useDocumentTitle("我的文件");
  const owner = useAuthStore((state) => state.userData?.id);
  const actions = useAuthStore((state) => state.platformWorkflowActions);
  const canRead = actions?.includes("file_read") ?? false;
  const canUpload = canRead && (actions?.includes("file_upload") ?? false);
  const canDownload = canRead && (actions?.includes("file_download") ?? false);
  const canRename = canRead && (actions?.includes("file_update") ?? false);
  const canDelete = canRead && (actions?.includes("file_delete") ?? false);
  const [editing, setEditing] = useState<PersonalFile | null>(null);
  const [name, setName] = useState("");
  const files = usePlatformFiles();
  const policy = usePlatformFilePolicy();
  const [selected, setSelected] = useState<File | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [progress, setProgress] = useState(0);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [preview, setPreview] = useState<{
    file: PersonalFile;
    text: string;
  } | null>(null);
  const upload = useRef<AbortController | null>(null);
  const read = useRef<{
    controller: AbortController;
    download: boolean;
  } | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<FileFilter>("all");
  const [sort, setSort] = useState<FileSort>("newest");
  const [page, setPage] = useState(1);
  const [dragging, setDragging] = useState(false);

  // 现有接口返回本人完整列表；搜索、排序和分页仅处理真实返回的数据。
  const visibleFiles = useMemo(() => {
    const keyword = search.trim().toLocaleLowerCase();
    return (files.data ?? [])
      .filter(
        (file) =>
          (!keyword || file.name.toLocaleLowerCase().includes(keyword)) &&
          (filter === "all" || categoryOf(file.name) === filter),
      )
      .sort((a, b) => {
        if (sort === "name")
          return a.name.localeCompare(b.name, "zh-CN", { numeric: true });
        if (sort === "size-desc") return b.size - a.size;
        if (sort === "size-asc") return a.size - b.size;
        const aTime = parseApiTimestamp(a.created_at)?.getTime() ?? 0;
        const bTime = parseApiTimestamp(b.created_at)?.getTime() ?? 0;
        return sort === "oldest" ? aTime - bTime : bTime - aTime;
      });
  }, [files.data, search, filter, sort]);
  const pageCount = Math.max(1, Math.ceil(visibleFiles.length / PAGE_SIZE));
  // 删除或刷新后列表可能缩短，当前页不能落到空白页。
  const currentPage = Math.min(page, pageCount);
  const pageFiles = visibleFiles.slice(
    (currentPage - 1) * PAGE_SIZE,
    currentPage * PAGE_SIZE,
  );
  const hasFilters = !!search.trim() || filter !== "all";
  const selectedAllowed =
    !!selected &&
    !!policy.data &&
    selected.size <= policy.data.max_bytes &&
    policy.data.extensions.includes(extensionOf(selected.name));

  const clearFilters = () => {
    setSearch("");
    setFilter("all");
    setPage(1);
  };

  const chooseFile = (file: File | undefined) => {
    if (!canUpload || busy || !policy.data || !file) return;
    setSelected(file);
    setMessage("");
    setError(
      file.size > policy.data.max_bytes ||
        !policy.data.extensions.includes(extensionOf(file.name))
        ? "文件大小或类型不符合上传限制，请重新选择。"
        : "",
    );
  };

  useEffect(() => {
    if (!canUpload) upload.current?.abort();
    // 文本预览只需读取权限，下载撤权不能误取消仍获授权的预览。
    if (!canRead || (read.current?.download && !canDownload))
      read.current?.controller.abort();
    if (!canRead) setPreview(null);
    if (!canRename) setEditing(null);
  }, [canRead, canUpload, canDownload, canRename]);
  useEffect(
    () => () => {
      upload.current?.abort();
      read.current?.controller.abort();
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
    read.current = { controller, download: !isPreview };
    setBusy(file.id);
    setError("");
    setMessage("");
    if (isPreview) setPreview(null);
    try {
      if (isPreview) {
        const text = await readPersonalFile(
          owner,
          file.id,
          true,
          controller.signal,
        );
        if (!controller.signal.aborted) setPreview({ file, text });
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
      if (read.current?.controller === controller) {
        read.current = null;
        setBusy(null);
      }
    }
  };

  if (!canRead)
    return (
      <div
        className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center"
        role="status"
      >
        <ShieldCheck
          className="h-10 w-10 text-muted-foreground"
          strokeWidth={1.5}
          aria-hidden="true"
        />
        <h1 className="text-lg font-semibold">暂时无法访问我的文件</h1>
        <p className="text-sm text-muted-foreground">
          需要平台个人文件读取权限，已有文件未改变。
        </p>
      </div>
    );
  return (
    <section
      className="h-full w-full overflow-y-auto"
      aria-labelledby="personal-files-title"
    >
      <div className="mx-auto flex w-full flex-col gap-6 px-5 pb-8 pt-10 xl:container">
        <header className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="mb-2 flex items-center gap-2">
              <SidebarTrigger className="lg:hidden" />
              <h1 id="personal-files-title" className="text-xl font-semibold">
                我的文件
              </h1>
              <span className="rounded-md border bg-muted/40 px-2 py-0.5 text-xs text-muted-foreground">
                个人空间
              </span>
            </div>
            <p className="text-sm text-muted-foreground">
              管理工作流需要的文件，上传一次，在多个流程中复用。
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              disabled={!!busy || files.isFetching}
              onClick={() => {
                refresh();
                void policy.refetch();
              }}
            >
              <RefreshCw
                className={cn(
                  "h-4 w-4",
                  files.isFetching && "animate-spin motion-reduce:animate-none",
                )}
                aria-hidden="true"
              />
              刷新列表
            </Button>
            {canUpload && (
              <Button
                disabled={!!busy || !policy.data}
                onClick={() => input.current?.click()}
              >
                <Upload className="h-4 w-4" aria-hidden="true" />
                上传文件
              </Button>
            )}
          </div>
        </header>

        {canUpload ? (
          <div className="space-y-3">
            <input
              ref={input}
              aria-label="选择文件"
              type="file"
              className="sr-only"
              tabIndex={-1}
              accept={policy.data?.extensions.join(",")}
              disabled={!!busy || !policy.data}
              onChange={(event) => chooseFile(event.target.files?.[0])}
            />
            <button
              type="button"
              aria-label="选择或拖入文件"
              disabled={!!busy || !policy.data}
              className={cn(
                "flex w-full flex-wrap items-center gap-4 rounded-xl border border-dashed bg-muted/20 p-5 text-left transition-colors hover:border-foreground/30 hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60",
                dragging && "border-primary bg-muted/60",
              )}
              onClick={() => input.current?.click()}
              onDragOver={(event) => {
                event.preventDefault();
                if (canUpload && !busy) setDragging(true);
              }}
              onDragLeave={(event) => {
                if (
                  !event.currentTarget.contains(
                    event.relatedTarget as Node | null,
                  )
                )
                  setDragging(false);
              }}
              onDrop={(event) => {
                event.preventDefault();
                setDragging(false);
                if (!canUpload || busy || !policy.data) return;
                if (event.dataTransfer.files.length !== 1) {
                  setMessage("");
                  setError("每次上传一个文件，请分别选择或拖入。");
                  return;
                }
                chooseFile(event.dataTransfer.files[0]);
              }}
            >
              <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl border bg-background">
                <CloudUpload
                  className="h-6 w-6 text-muted-foreground"
                  strokeWidth={1.5}
                  aria-hidden="true"
                />
              </span>
              <span className="min-w-0 flex-1 space-y-1">
                <span className="block text-sm font-medium">
                  {dragging ? "松开以选择文件" : "点击选择，或将文件拖到这里"}
                </span>
                <span className="block text-xs leading-5 text-muted-foreground">
                  {policy.data
                    ? `单文件最大 ${formatFileSize(policy.data.max_bytes)} · 支持 ${policy.data.extensions.map((extension) => extension.slice(1).toUpperCase()).join("、")}`
                    : "正在获取上传限制…"}
                </span>
              </span>
              <span className="rounded-md border bg-background px-3 py-1.5 text-xs font-medium">
                选择文件
              </span>
            </button>
            {selected && (
              <div className="flex flex-wrap items-center gap-3 rounded-lg border bg-background p-4">
                <FileIcon name={selected.name} />
                <div className="min-w-0 flex-1">
                  <p className="break-all text-sm font-medium">
                    {selected.name}
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {formatFileSize(selected.size)} ·{" "}
                    {busy === "upload" ? "正在上传" : "待上传"}
                  </p>
                </div>
                {busy === "upload" ? (
                  <>
                    <span
                      role="status"
                      className="text-xs text-muted-foreground"
                    >
                      {progress < 100 ? `上传中 ${progress}%` : "服务端校验中"}
                    </span>
                    <progress
                      aria-label="文件上传进度"
                      value={progress}
                      max={100}
                      className="h-2 w-28 accent-primary"
                    />
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => upload.current?.abort()}
                    >
                      取消上传
                    </Button>
                  </>
                ) : (
                  <>
                    <Button
                      size="sm"
                      disabled={!selectedAllowed || !!busy}
                      onClick={handleUpload}
                    >
                      <Upload aria-hidden="true" />
                      上传
                    </Button>
                    <Button
                      size="icon"
                      variant="ghost"
                      aria-label="移除待上传文件"
                      disabled={!!busy}
                      onClick={() => {
                        setSelected(null);
                        setError("");
                        if (input.current) input.current.value = "";
                      }}
                    >
                      <X aria-hidden="true" />
                    </Button>
                  </>
                )}
              </div>
            )}
          </div>
        ) : (
          <div
            className="flex items-center gap-2 rounded-lg border bg-muted/20 px-4 py-3 text-sm text-muted-foreground"
            role="status"
          >
            <ShieldCheck className="h-4 w-4 shrink-0" aria-hidden="true" />
            当前没有上传权限，可以浏览已有文件。
          </div>
        )}

        {policy.error && (
          <div
            role="alert"
            className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm"
          >
            <AlertCircle
              className="h-4 w-4 shrink-0 text-destructive"
              aria-hidden="true"
            />
            {extractApiErrorMessages(policy.error).join("；")}
          </div>
        )}
        {error && !editing && (
          <div
            role="alert"
            className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm"
          >
            <AlertCircle
              className="h-4 w-4 shrink-0 text-destructive"
              aria-hidden="true"
            />
            {error}
          </div>
        )}
        {message && (
          <div
            role="status"
            className="flex items-center gap-2 rounded-lg border bg-muted/30 p-3 text-sm"
          >
            <CheckCircle2 className="h-4 w-4 shrink-0" aria-hidden="true" />
            {message}
          </div>
        )}

        <div className="overflow-hidden rounded-xl border bg-background">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b p-4">
            <div className="w-full sm:w-72 lg:w-80">
              <Input
                type="search"
                icon="Search"
                inputClassName="[&::-webkit-search-cancel-button]:appearance-none"
                aria-label="搜索文件"
                placeholder="搜索文件名称…"
                value={search}
                onChange={(event) => {
                  setSearch(event.target.value);
                  setPage(1);
                }}
                endIcon={
                  search ? (
                    <button
                      type="button"
                      aria-label="清除搜索"
                      className="rounded-sm text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      onClick={() => {
                        setSearch("");
                        setPage(1);
                      }}
                    >
                      <X className="h-3.5 w-3.5" aria-hidden="true" />
                    </button>
                  ) : undefined
                }
              />
            </div>
            <div className="relative">
              <select
                aria-label="排序方式"
                value={sort}
                onChange={(event) => {
                  setSort(event.target.value as FileSort);
                  setPage(1);
                }}
                className="h-9 appearance-none rounded-md border border-control bg-background py-1 pl-3 pr-9 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <option value="newest">最新上传</option>
                <option value="oldest">最早上传</option>
                <option value="name">名称 A–Z</option>
                <option value="size-desc">大小从大到小</option>
                <option value="size-asc">大小从小到大</option>
              </select>
              <ChevronDown
                className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
                aria-hidden="true"
              />
            </div>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-3 border-b px-4 py-3">
            <div
              className="flex flex-wrap gap-1"
              role="group"
              aria-label="文件类型筛选"
            >
              {FILE_FILTERS.map((item) => (
                <Button
                  key={item.value}
                  size="sm"
                  variant="ghost"
                  aria-label={item.accessibleLabel}
                  aria-pressed={filter === item.value}
                  className={cn(
                    "h-8 text-muted-foreground",
                    filter === item.value &&
                      "bg-muted font-medium text-foreground",
                  )}
                  onClick={() => {
                    setFilter(item.value);
                    setPage(1);
                  }}
                >
                  {item.label}
                </Button>
              ))}
            </div>
            {!files.isPending && !files.error && (
              <span
                className="text-xs text-muted-foreground"
                aria-live="polite"
              >
                {hasFilters
                  ? `匹配 ${visibleFiles.length} 个 / 共 ${files.data?.length ?? 0} 个文件`
                  : `共 ${files.data?.length ?? 0} 个文件`}
              </span>
            )}
          </div>
          {files.isPending ? (
            <div
              className="space-y-5 p-5"
              role="status"
              aria-label="正在加载文件"
            >
              {[0, 1, 2, 3].map((row) => (
                <div className="flex items-center gap-3" key={row}>
                  <Skeleton className="h-10 w-10 rounded-lg" />
                  <Skeleton className="h-4 w-1/3" />
                  <Skeleton className="ml-auto h-4 w-20" />
                </div>
              ))}
              <span className="sr-only">正在加载文件…</span>
            </div>
          ) : files.error ? (
            <div
              className="flex flex-col items-center gap-3 px-6 py-16 text-center"
              role="alert"
            >
              <AlertCircle
                className="h-8 w-8 text-muted-foreground"
                strokeWidth={1.5}
                aria-hidden="true"
              />
              <p className="font-medium">文件列表加载失败</p>
              <p className="max-w-lg break-words text-sm text-muted-foreground">
                {extractApiErrorMessages(files.error).join("；")}
              </p>
              <Button variant="outline" onClick={refresh}>
                重新加载
              </Button>
            </div>
          ) : visibleFiles.length === 0 ? (
            <div
              className="flex flex-col items-center gap-3 px-6 py-16 text-center"
              role="status"
            >
              <span className="mb-1 rounded-2xl border bg-muted/30 p-4">
                {hasFilters ? (
                  <Search
                    className="h-7 w-7 text-muted-foreground"
                    strokeWidth={1.5}
                    aria-hidden="true"
                  />
                ) : (
                  <FolderOpen
                    className="h-7 w-7 text-muted-foreground"
                    strokeWidth={1.5}
                    aria-hidden="true"
                  />
                )}
              </span>
              <p className="font-medium">
                {hasFilters ? "没有找到匹配的文件" : "还没有上传文件"}
              </p>
              <p className="text-sm text-muted-foreground">
                {hasFilters
                  ? "试试其他关键词或文件类型。"
                  : canUpload
                    ? "上传第一份文件，在工作流的文件节点中选择使用。"
                    : "当前个人空间中暂无文件。"}
              </p>
              {hasFilters ? (
                <Button variant="outline" onClick={clearFilters}>
                  清除筛选
                </Button>
              ) : (
                canUpload && (
                  <Button
                    disabled={!policy.data || !!busy}
                    onClick={() => input.current?.click()}
                  >
                    <Upload aria-hidden="true" />
                    选择第一个文件
                  </Button>
                )
              )}
            </div>
          ) : (
            <>
              <div className="overflow-x-auto">
                <Table className="min-w-[680px]" aria-label="个人文件列表">
                  <TableHeader className="bg-muted/20">
                    <TableRow className="hover:bg-transparent">
                      <TableHead className="w-[40%] pl-5">文件名称</TableHead>
                      <TableHead>类型</TableHead>
                      <TableHead>大小</TableHead>
                      <TableHead className="whitespace-nowrap">
                        上传时间
                      </TableHead>
                      <TableHead className="whitespace-nowrap">
                        最近修改
                      </TableHead>
                      <TableHead className="pr-5 text-right">操作</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {pageFiles.map((file) => {
                      const canPreview =
                        !!policy.data &&
                        file.size <= policy.data.preview_bytes &&
                        /\.(txt|md|csv|json)$/i.test(file.name);
                      return (
                        <TableRow key={file.id}>
                          <TableCell className="py-4 pl-5">
                            <div className="flex items-center gap-3">
                              <FileIcon name={file.name} />
                              <span className="min-w-0 break-all font-medium">
                                {file.name}
                              </span>
                            </div>
                          </TableCell>
                          <TableCell>
                            <span className="rounded-md border bg-muted/20 px-2 py-1 text-xs text-muted-foreground">
                              {extensionOf(file.name).slice(1).toUpperCase() ||
                                "—"}
                            </span>
                          </TableCell>
                          <TableCell className="whitespace-nowrap text-muted-foreground">
                            {formatFileSize(file.size)}
                          </TableCell>
                          <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                            {displayDate(file.created_at)}
                          </TableCell>
                          <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                            {displayDate(file.updated_at)}
                          </TableCell>
                          <TableCell className="pr-5">
                            <div className="flex justify-end gap-1">
                              <Button
                                variant="ghost"
                                size="icon"
                                className="h-8 w-8 text-muted-foreground"
                                aria-label={`预览 ${file.name}`}
                                title={
                                  canPreview
                                    ? "文本预览"
                                    : `仅支持不超过 ${policy.data ? formatFileSize(policy.data.preview_bytes) : "预览限制"} 的 TXT、MD、CSV、JSON 文件`
                                }
                                disabled={!!busy || !canPreview}
                                onClick={() => handleRead(file, true)}
                              >
                                {busy === file.id ? (
                                  <Loader2
                                    className="animate-spin motion-reduce:animate-none"
                                    aria-hidden="true"
                                  />
                                ) : (
                                  <Eye aria-hidden="true" />
                                )}
                              </Button>
                              {canDownload && (
                                <Button
                                  variant="ghost"
                                  size="icon"
                                  className="h-8 w-8 text-muted-foreground"
                                  title="下载"
                                  aria-label={`下载 ${file.name}`}
                                  disabled={!!busy}
                                  onClick={() => handleRead(file, false)}
                                >
                                  <Download aria-hidden="true" />
                                </Button>
                              )}
                              {canRename && (
                                <Button
                                  variant="ghost"
                                  size="icon"
                                  className="h-8 w-8 text-muted-foreground"
                                  title="重命名"
                                  aria-label={`重命名 ${file.name}`}
                                  disabled={!!busy}
                                  onClick={() => {
                                    setEditing(file);
                                    setName(file.name);
                                    setError("");
                                    setMessage("");
                                  }}
                                >
                                  <Pencil aria-hidden="true" />
                                </Button>
                              )}
                              {canDelete && (
                                <DeleteConfirmationModal
                                  asChild
                                  description={file.name}
                                  onConfirm={() => modify(file, true)}
                                  note="被流程或历史版本引用的文件需先解除引用。"
                                >
                                  <Button
                                    variant="ghost"
                                    size="icon"
                                    className="h-8 w-8 text-muted-foreground hover:text-destructive"
                                    title="删除"
                                    aria-label={`删除 ${file.name}`}
                                    disabled={!!busy}
                                  >
                                    <Trash2 aria-hidden="true" />
                                  </Button>
                                </DeleteConfirmationModal>
                              )}
                            </div>
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </div>
              <div className="flex flex-wrap items-center justify-between gap-3 border-t px-5 py-3">
                <span className="text-xs text-muted-foreground">
                  显示 {(currentPage - 1) * PAGE_SIZE + 1}–
                  {Math.min(currentPage * PAGE_SIZE, visibleFiles.length)}{" "}
                  项，共 {visibleFiles.length} 项
                </span>
                <nav className="flex items-center gap-2" aria-label="文件分页">
                  <Button
                    variant="outline"
                    size="icon"
                    className="h-8 w-8"
                    aria-label="上一页"
                    disabled={currentPage === 1}
                    onClick={() => setPage(currentPage - 1)}
                  >
                    <ChevronLeft aria-hidden="true" />
                  </Button>
                  <span
                    className="min-w-16 text-center text-xs text-muted-foreground"
                    aria-live="polite"
                  >
                    第 {currentPage} / {pageCount} 页
                  </span>
                  <Button
                    variant="outline"
                    size="icon"
                    className="h-8 w-8"
                    aria-label="下一页"
                    disabled={currentPage === pageCount}
                    onClick={() => setPage(currentPage + 1)}
                  >
                    <ChevronRight aria-hidden="true" />
                  </Button>
                </nav>
              </div>
            </>
          )}
        </div>
        <div className="flex items-start gap-2 text-xs leading-5 text-muted-foreground">
          <Info className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          <p>
            仅显示本人文件，操作按平台权限开放。被流程或历史版本引用的文件需先解除引用，才能重命名或删除。
            {!canDownload && " 当前没有下载权限，可预览符合限制的文本。"}
          </p>
        </div>
      </div>

      <Dialog
        open={!!editing && canRename}
        onOpenChange={(open) => {
          if (!open && busy !== "rename") {
            setEditing(null);
            setError("");
          }
        }}
      >
        <DialogContent
          className="w-[calc(100%_-_2rem)] max-w-md"
          onInteractOutside={(event) => {
            if (busy === "rename") event.preventDefault();
          }}
          onEscapeKeyDown={(event) => {
            if (busy === "rename") event.preventDefault();
          }}
        >
          <DialogHeader>
            <DialogTitle>重命名文件</DialogTitle>
            <DialogDescription>
              请保留文件扩展名，被流程或历史版本引用的文件需先解除引用。
            </DialogDescription>
          </DialogHeader>
          <form
            className="space-y-5"
            onSubmit={(event) => {
              event.preventDefault();
              if (editing) void modify(editing);
            }}
          >
            {editing && (
              <div className="space-y-2">
                <label
                  htmlFor="personal-file-name"
                  className="text-sm font-medium"
                >
                  文件名称
                </label>
                <Input
                  id="personal-file-name"
                  aria-label={`新文件名 ${editing.name}`}
                  value={name}
                  disabled={!!busy}
                  onChange={(event) => setName(event.target.value)}
                />
              </div>
            )}
            {error && (
              <p role="alert" className="break-words text-sm text-destructive">
                {error}
              </p>
            )}
            <DialogFooter>
              <Button
                variant="outline"
                disabled={!!busy}
                onClick={() => {
                  setEditing(null);
                  setError("");
                }}
              >
                取消
              </Button>
              <Button
                type="submit"
                loading={busy === "rename"}
                disabled={!!busy || !name.trim() || !canRename}
              >
                保存名称
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
      <Dialog
        open={!!preview && canRead}
        onOpenChange={(open) => {
          if (!open) setPreview(null);
        }}
      >
        <DialogContent className="max-h-[85vh] w-[calc(100%_-_2rem)] max-w-3xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-3 pr-6">
              {preview && <FileIcon name={preview.file.name} />}
              <span className="min-w-0 break-all">{preview?.file.name}</span>
            </DialogTitle>
            <DialogDescription>
              文本预览 · {preview && formatFileSize(preview.file.size)}
            </DialogDescription>
          </DialogHeader>
          <pre className="min-h-40 overflow-auto whitespace-pre-wrap break-words rounded-lg border bg-muted/20 p-4 font-mono text-sm leading-6">
            {preview?.text || (preview ? "（空文件）" : "")}
          </pre>
          <DialogFooter>
            <Button variant="outline" onClick={() => setPreview(null)}>
              关闭预览
            </Button>
            {canDownload && preview && (
              <Button
                disabled={!!busy}
                onClick={() => handleRead(preview.file, false)}
              >
                <Download aria-hidden="true" />
                下载文件
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}
