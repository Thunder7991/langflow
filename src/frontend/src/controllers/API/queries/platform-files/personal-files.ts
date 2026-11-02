import { useQuery } from "@tanstack/react-query";
import { handlePlatformSsoError } from "@/customization/utils/platform-sso-error";
import useAuthStore from "@/stores/authStore";
import { api } from "../../api";
import { getURL } from "../../helpers/constants";

export type PersonalFile = {
  id: string;
  name: string;
  path: string;
  user_id: string;
  size: number;
  created_at: string;
  updated_at: string;
};
export type PersonalFilePolicy = {
  max_bytes: number;
  extensions: string[];
  preview_bytes: number;
};
const url = () => getURL("PLATFORM_FILES", {}, true);

export async function renamePersonalFile(
  owner: string,
  id: string,
  name: string,
) {
  assertAccess(owner, "file_update");
  await api.patch(`${url()}/${encodeURIComponent(id)}`, { name });
  assertAccess(owner, "file_update");
}

export async function deletePersonalFile(owner: string, id: string) {
  assertAccess(owner, "file_delete");
  await api.delete(`${url()}/${encodeURIComponent(id)}`);
  assertAccess(owner, "file_delete");
}

/** 身份及能力在响应到达时再次比较，旧请求不能给切换后的页面恢复数据。 */
function assertAccess(owner: string, action: string, signal?: AbortSignal) {
  const state = useAuthStore.getState();
  if (
    signal?.aborted ||
    state.userData?.id !== owner ||
    !state.platformWorkflowActions?.includes("file_read") ||
    !state.platformWorkflowActions.includes(action)
  ) {
    throw new Error("个人文件身份或权限已变化，请重新核对当前页面。");
  }
}

export function usePlatformFiles() {
  const owner = useAuthStore((state) => state.userData?.id);
  const canRead = useAuthStore(
    (state) => state.platformWorkflowActions?.includes("file_read") ?? false,
  );
  return useQuery({
    queryKey: ["platform-files", owner],
    enabled: Boolean(owner && canRead),
    queryFn: async ({ signal }) => {
      assertAccess(owner!, "file_read", signal);
      const response = await api.get<PersonalFile[]>(url(), { signal });
      assertAccess(owner!, "file_read", signal);
      return response.data;
    },
    gcTime: 0,
    staleTime: 0,
    retry: false,
  });
}

export function usePlatformFilePolicy() {
  const owner = useAuthStore((state) => state.userData?.id);
  const canRead = useAuthStore(
    (state) => state.platformWorkflowActions?.includes("file_read") ?? false,
  );
  return useQuery({
    queryKey: ["platform-file-policy", owner],
    enabled: Boolean(owner && canRead),
    queryFn: async ({ signal }) => {
      assertAccess(owner!, "file_read", signal);
      const response = await api.get<PersonalFilePolicy>(`${url()}/policy`, {
        signal,
      });
      assertAccess(owner!, "file_read", signal);
      return response.data;
    },
    gcTime: 0,
    staleTime: 0,
    retry: false,
  });
}

export async function uploadPersonalFile(
  owner: string,
  file: File,
  signal: AbortSignal,
  progress: (percentage: number) => void,
): Promise<PersonalFile> {
  assertAccess(owner, "file_upload", signal);
  const response = await api.post<PersonalFile>(url(), file, {
    signal,
    headers: {
      "Content-Type": "application/octet-stream",
      "X-Workflow-Filename": encodeURIComponent(file.name),
    },
    onUploadProgress: (event) => {
      if (!signal.aborted && event.total)
        progress(Math.min(100, Math.round((event.loaded / event.total) * 100)));
    },
  });
  assertAccess(owner, "file_upload", signal);
  return response.data;
}

export async function readPersonalFile(
  owner: string,
  id: string,
  preview: true,
  signal: AbortSignal,
): Promise<string>;
export async function readPersonalFile(
  owner: string,
  id: string,
  preview: false,
  signal: AbortSignal,
): Promise<Blob>;
export async function readPersonalFile(
  owner: string,
  id: string,
  preview: boolean,
  signal: AbortSignal,
): Promise<string | Blob> {
  const action = preview ? "file_read" : "file_download";
  assertAccess(owner, action, signal);
  try {
    const response = await api.get(
      `${url()}/${encodeURIComponent(id)}${preview ? "/content" : ""}`,
      {
        signal,
        responseType: preview ? "text" : "blob",
        // 文本预览保持原文，JSON 文件不能被 axios 默认转换为对象。
        transformResponse: [(data) => data],
      },
    );
    assertAccess(owner, action, signal);
    return response.data;
  } catch (error) {
    // responseType 会影响错误正文。还原 FastAPI 错误，避免把 JSON Blob 当下载成功。
    const response = (error as { response?: { data?: unknown } }).response;
    try {
      if (response?.data instanceof Blob) {
        response.data = JSON.parse(await response.data.text());
        handlePlatformSsoError(response.data);
      } else if (typeof response?.data === "string") {
        response.data = JSON.parse(response.data);
        handlePlatformSsoError(response.data);
      }
    } catch {
      /* 非 JSON 的代理错误由统一错误提示处理。 */
    }
    throw error;
  }
}
