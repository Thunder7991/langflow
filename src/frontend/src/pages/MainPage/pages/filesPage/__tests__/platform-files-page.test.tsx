import {
  act,
  fireEvent,
  render as renderComponent,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import type { ReactElement } from "react";
import { SidebarProvider } from "@/components/ui/sidebar";
import {
  deletePersonalFile,
  type PersonalFile,
  readPersonalFile,
  renamePersonalFile,
  uploadPersonalFile,
  usePlatformFilePolicy,
  usePlatformFiles,
} from "@/controllers/API/queries/platform-files/personal-files";
import useAuthStore from "@/stores/authStore";
import PlatformFilesPage from "../platform-files-page";

jest.mock("@/controllers/API/queries/platform-files/personal-files", () => ({
  usePlatformFiles: jest.fn(),
  usePlatformFilePolicy: jest.fn(),
  uploadPersonalFile: jest.fn(),
  readPersonalFile: jest.fn(),
  renamePersonalFile: jest.fn(),
  deletePersonalFile: jest.fn(),
}));
const render = (element: ReactElement) =>
  renderComponent(<SidebarProvider>{element}</SidebarProvider>);
const personalFile = (id: string, name: string, size = 3): PersonalFile => ({
  id,
  name,
  size,
  path: `owner/${id}.${name.split(".").pop()}`,
  user_id: "owner",
  created_at: "2026-11-08T00:00:00Z",
  updated_at: "2026-11-08T00:00:00Z",
});
const refetch = jest.fn();
beforeEach(() => {
  jest.clearAllMocks();
  useAuthStore.setState({
    userData: { id: "owner" } as never,
    platformWorkflowActions: [
      "read",
      "file_read",
      "file_upload",
      "file_download",
    ],
  });
  (usePlatformFiles as jest.Mock).mockReturnValue({
    data: [personalFile("one", "资料.txt")],
    refetch,
    isPending: false,
    isFetching: false,
  });
  (usePlatformFilePolicy as jest.Mock).mockReturnValue({
    data: { max_bytes: 20, extensions: [".txt"], preview_bytes: 10 },
    isPending: false,
    refetch: jest.fn(),
  });
});
afterEach(() =>
  useAuthStore.setState({ userData: null, platformWorkflowActions: null }),
);

it("renames a personal file only with an independent grant and refreshes the list", async () => {
  useAuthStore.setState({
    platformWorkflowActions: ["read", "file_read", "file_update"],
  });
  (renamePersonalFile as jest.Mock).mockResolvedValue(undefined);
  render(<PlatformFilesPage />);
  fireEvent.click(screen.getByRole("button", { name: "重命名 资料.txt" }));
  fireEvent.change(screen.getByLabelText("新文件名 资料.txt"), {
    target: { value: "调试.txt" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存名称" }));
  expect(await screen.findByText("文件已重命名。")).toBeInTheDocument();
  expect(renamePersonalFile).toHaveBeenCalledWith("owner", "one", "调试.txt");
  expect(refetch).toHaveBeenCalled();
  expect(
    screen.queryByRole("button", { name: "删除" }),
  ).not.toBeInTheDocument();
});

it("shows independent read/upload/download capabilities and escaped text preview", async () => {
  (readPersonalFile as jest.Mock).mockResolvedValue("<script>unsafe</script>");
  render(<PlatformFilesPage />);
  fireEvent.click(screen.getByRole("button", { name: "预览 资料.txt" }));
  expect(
    await screen.findByText("<script>unsafe</script>"),
  ).toBeInTheDocument();
  expect(document.querySelector("script")).toBeNull();
  act(() => {
    useAuthStore.setState({ platformWorkflowActions: ["read", "file_read"] });
  });
  await waitFor(() =>
    expect(
      screen.queryByRole("button", { name: "下载 资料.txt" }),
    ).not.toBeInTheDocument(),
  );
  expect(screen.queryByLabelText("选择文件")).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "上传" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: /删除|重命名/ }),
  ).not.toBeInTheDocument();
});

it("keeps the selected file on failure and refetches to resolve uncertain uploads", async () => {
  (uploadPersonalFile as jest.Mock).mockRejectedValue(
    new Error("network unavailable"),
  );
  render(<PlatformFilesPage />);
  fireEvent.change(screen.getByLabelText("选择文件"), {
    target: { files: [new File(["abc"], "new.txt")] },
  });
  fireEvent.click(screen.getByRole("button", { name: "上传" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "network unavailable",
  );
  expect(screen.getByRole("button", { name: "上传" })).toBeEnabled();
  expect(screen.queryByText(/上传成功/)).not.toBeInTheDocument();
  expect(refetch).toHaveBeenCalled();
});

it("cancels in-flight upload on permission removal and never reports late success", async () => {
  let finish!: (value: unknown) => void;
  (uploadPersonalFile as jest.Mock).mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const view = render(<PlatformFilesPage />);
  fireEvent.change(screen.getByLabelText("选择文件"), {
    target: { files: [new File(["abc"], "new.txt")] },
  });
  fireEvent.click(screen.getByRole("button", { name: "上传" }));
  const signal = (uploadPersonalFile as jest.Mock).mock
    .calls[0][2] as AbortSignal;
  act(() => {
    useAuthStore.setState({ platformWorkflowActions: ["read", "file_read"] });
  });
  await waitFor(() => expect(signal.aborted).toBe(true));
  finish({ id: "late" });
  await waitFor(() =>
    expect(screen.queryByText(/上传成功/)).not.toBeInTheDocument(),
  );
  view.unmount();
});

it("combines filename search with file type filters", () => {
  (usePlatformFiles as jest.Mock).mockReturnValue({
    data: [
      personalFile("one", "季度报告.pdf"),
      personalFile("two", "季度数据.csv"),
      personalFile("three", "封面.png"),
    ],
    refetch,
    isPending: false,
  });
  render(<PlatformFilesPage />);
  fireEvent.change(screen.getByRole("searchbox", { name: "搜索文件" }), {
    target: { value: "季度" },
  });
  expect(screen.getByText("季度报告.pdf")).toBeInTheDocument();
  expect(screen.queryByText("封面.png")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "数据文件" }));
  expect(screen.getByText("季度数据.csv")).toBeInTheDocument();
  expect(screen.queryByText("季度报告.pdf")).not.toBeInTheDocument();
});

it("resets pagination when searching and recovers when the list shrinks", () => {
  const data = Array.from({ length: 12 }, (_, index) =>
    personalFile(
      String(index),
      `文件${String(index + 1).padStart(2, "0")}.txt`,
    ),
  );
  (usePlatformFiles as jest.Mock).mockReturnValue({
    data,
    refetch,
    isPending: false,
  });
  const view = render(<PlatformFilesPage />);
  expect(screen.queryByText("文件12.txt")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  expect(screen.getByText("文件12.txt")).toBeInTheDocument();
  fireEvent.change(screen.getByRole("searchbox", { name: "搜索文件" }), {
    target: { value: "文件01" },
  });
  expect(screen.getByText("文件01.txt")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "清除搜索" }));
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  (usePlatformFiles as jest.Mock).mockReturnValue({
    data: [data[0]],
    refetch,
    isPending: false,
  });
  view.rerender(
    <SidebarProvider>
      <PlatformFilesPage />
    </SidebarProvider>,
  );
  expect(screen.getByText("文件01.txt")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "下一页" })).toBeDisabled();
});

it("sorts real file sizes while preserving a zero byte file", () => {
  (usePlatformFiles as jest.Mock).mockReturnValue({
    data: [
      personalFile("large", "大文件.txt", 2048),
      personalFile("empty", "空文件.txt", 0),
    ],
    refetch,
    isPending: false,
  });
  render(<PlatformFilesPage />);
  fireEvent.change(screen.getByRole("combobox", { name: "排序方式" }), {
    target: { value: "size-asc" },
  });
  const rows = within(screen.getByRole("table")).getAllByRole("row");
  expect(rows[1]).toHaveTextContent("空文件.txt");
  expect(rows[1]).toHaveTextContent("0 Bytes");
  expect(rows[2]).toHaveTextContent("2 KB");
});

it("clears unmatched filters without confusing them with an empty library", () => {
  render(<PlatformFilesPage />);
  fireEvent.change(screen.getByRole("searchbox", { name: "搜索文件" }), {
    target: { value: "不存在" },
  });
  expect(screen.getByText("没有找到匹配的文件")).toBeInTheDocument();
  expect(screen.queryByText("还没有上传文件")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "清除筛选" }));
  expect(screen.getByText("资料.txt")).toBeInTheDocument();
});

it("rejects a multiple file drop without choosing an arbitrary file", () => {
  render(<PlatformFilesPage />);
  fireEvent.drop(screen.getByRole("button", { name: "选择或拖入文件" }), {
    dataTransfer: {
      files: [new File(["a"], "a.txt"), new File(["b"], "b.txt")],
      types: ["Files"],
    },
  });
  expect(screen.getByRole("alert")).toHaveTextContent("每次上传一个文件");
  expect(
    screen.queryByRole("button", { name: "上传" }),
  ).not.toBeInTheDocument();
});

it("keeps an authorized text preview in flight when download permission is revoked", async () => {
  let finish!: (value: string) => void;
  (readPersonalFile as jest.Mock).mockImplementation(
    () =>
      new Promise<string>((resolve) => {
        finish = resolve;
      }),
  );
  render(<PlatformFilesPage />);
  fireEvent.click(screen.getByRole("button", { name: "预览 资料.txt" }));
  const signal = (readPersonalFile as jest.Mock).mock
    .calls[0][3] as AbortSignal;
  act(() => {
    useAuthStore.setState({ platformWorkflowActions: ["read", "file_read"] });
  });
  expect(signal.aborted).toBe(false);
  await act(async () => finish("可读取的内容"));
  expect(await screen.findByText("可读取的内容")).toBeInTheDocument();
});

it("preserves the rename draft and shows a reference conflict in the dialog", async () => {
  useAuthStore.setState({
    platformWorkflowActions: ["read", "file_read", "file_update"],
  });
  (renamePersonalFile as jest.Mock).mockRejectedValue(
    new Error("文件被流程或历史版本引用"),
  );
  render(<PlatformFilesPage />);
  fireEvent.click(screen.getByRole("button", { name: "重命名 资料.txt" }));
  fireEvent.change(screen.getByLabelText("新文件名 资料.txt"), {
    target: { value: "新名称.txt" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存名称" }));
  const dialog = screen.getByRole("dialog");
  expect(await within(dialog).findByRole("alert")).toHaveTextContent(
    "文件被流程或历史版本引用",
  );
  expect(within(dialog).getByRole("textbox")).toHaveValue("新名称.txt");
});

it("requires confirmation before deleting an independently authorized file", async () => {
  useAuthStore.setState({
    platformWorkflowActions: ["read", "file_read", "file_delete"],
  });
  (deletePersonalFile as jest.Mock).mockResolvedValue(undefined);
  render(<PlatformFilesPage />);
  fireEvent.click(screen.getByRole("button", { name: "删除 资料.txt" }));
  fireEvent.click(screen.getByTestId("btn_cancel_delete_confirmation_modal"));
  expect(deletePersonalFile).not.toHaveBeenCalled();
  expect(screen.getByText("资料.txt")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "删除 资料.txt" }));
  fireEvent.click(screen.getByTestId("btn_delete_delete_confirmation_modal"));
  expect(await screen.findByText("文件已删除。")).toBeInTheDocument();
  expect(deletePersonalFile).toHaveBeenCalledWith("owner", "one");
});
