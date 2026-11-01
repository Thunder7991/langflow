import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import {
  readPersonalFile,
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
}));
jest.mock("@/components/ui/button", () => ({
  Button: ({ children, variant, ...props }) => (
    <button {...props}>{children}</button>
  ),
}));
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
    data: [{ id: "one", name: "资料.txt", size: 3 }],
    refetch,
    isPending: false,
    isFetching: false,
  });
  (usePlatformFilePolicy as jest.Mock).mockReturnValue({
    data: { max_bytes: 20, extensions: [".txt"], preview_bytes: 10 },
    isPending: false,
  });
});
afterEach(() =>
  useAuthStore.setState({ userData: null, platformWorkflowActions: null }),
);

it("shows independent read/upload/download capabilities and escaped text preview", async () => {
  (readPersonalFile as jest.Mock).mockResolvedValue("<script>unsafe</script>");
  render(<PlatformFilesPage />);
  fireEvent.click(screen.getByRole("button", { name: "预览 资料.txt" }));
  expect(
    await screen.findByText("<script>unsafe</script>"),
  ).toBeInTheDocument();
  expect(document.querySelector("script")).toBeNull();
  useAuthStore.setState({ platformWorkflowActions: ["read", "file_read"] });
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
  useAuthStore.setState({ platformWorkflowActions: ["read", "file_read"] });
  await waitFor(() => expect(signal.aborted).toBe(true));
  finish({ id: "late" });
  await waitFor(() =>
    expect(screen.queryByText(/上传成功/)).not.toBeInTheDocument(),
  );
  view.unmount();
});
