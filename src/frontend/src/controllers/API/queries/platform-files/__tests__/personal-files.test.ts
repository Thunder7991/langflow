import { api } from "@/controllers/API/api";
import useAuthStore from "@/stores/authStore";
import { readPersonalFile, uploadPersonalFile } from "../personal-files";

jest.mock("@/controllers/API/api", () => ({
  api: { post: jest.fn(), get: jest.fn() },
}));
const owner = "b4-owner";
beforeEach(() => {
  jest.clearAllMocks();
  useAuthStore.setState({
    userData: { id: owner } as never,
    platformWorkflowActions: [
      "read",
      "file_read",
      "file_upload",
      "file_download",
    ],
  });
});
afterEach(() =>
  useAuthStore.setState({ userData: null, platformWorkflowActions: null }),
);

it("uploads raw bytes with an encoded original name and cancellation signal", async () => {
  const file = new File(["abc"], "资料.txt");
  const controller = new AbortController();
  (api.post as jest.Mock).mockResolvedValue({
    data: { id: "one", name: file.name },
  });
  await uploadPersonalFile(owner, file, controller.signal, jest.fn());
  expect(api.post).toHaveBeenCalledWith(
    expect.stringContaining("/api/v2/platform-files"),
    file,
    expect.objectContaining({
      signal: controller.signal,
      headers: {
        "Content-Type": "application/octet-stream",
        "X-Workflow-Filename": encodeURIComponent(file.name),
      },
    }),
  );
});

it("rejects new calls after file revocation and late results after identity changes", async () => {
  useAuthStore.setState({ platformWorkflowActions: ["read", "update"] });
  await expect(
    readPersonalFile(owner, "one", false, new AbortController().signal),
  ).rejects.toThrow();
  expect(api.get).not.toHaveBeenCalled();
  useAuthStore.setState({
    platformWorkflowActions: ["read", "file_read", "file_download"],
  });
  (api.get as jest.Mock).mockImplementation(async () => {
    useAuthStore.setState({ userData: { id: "other" } as never });
    return { data: new Blob(["private"]) };
  });
  await expect(
    readPersonalFile(owner, "one", false, new AbortController().signal),
  ).rejects.toThrow();
});

it("decodes blob error details without reporting a successful download", async () => {
  const body = new Blob([]);
  body.text = async () => JSON.stringify({ detail: "File not found" });
  (api.get as jest.Mock).mockRejectedValue({
    response: { status: 404, data: body },
  });
  await expect(
    readPersonalFile(owner, "one", false, new AbortController().signal),
  ).rejects.toMatchObject({ response: { data: { detail: "File not found" } } });
});

it("notifies the host after decoding an expired SSO text response", async () => {
  const notify = jest
    .spyOn(window.parent, "postMessage")
    .mockImplementation(() => undefined);
  (api.get as jest.Mock).mockRejectedValue({
    response: {
      status: 401,
      data: JSON.stringify({
        code: "PLATFORM_SSO_REQUIRED",
        hostOrigin: "https://ai.example.com",
      }),
    },
  });
  await expect(
    readPersonalFile(owner, "one", true, new AbortController().signal),
  ).rejects.toBeDefined();
  expect(notify).toHaveBeenCalledWith(
    { channel: "workflow-sso", status: "expired" },
    "https://ai.example.com",
  );
  notify.mockRestore();
});
