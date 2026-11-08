import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { api } from "@/controllers/API/api";
import useAuthStore from "@/stores/authStore";
import useFlowStore from "@/stores/flowStore";
import useFlowsManagerStore from "@/stores/flowsManagerStore";
import PlatformAccessMenu from "../components/platform-access-menu";
import PlatformDryRunButton from "../components/platform-dry-run-button";

const mockEnqueue = jest.fn();
jest.mock("@/hooks/flows/use-autosave-flow", () => ({
  __esModule: true,
  default: () => ({ enqueue: mockEnqueue }),
}));
jest.mock("@/controllers/API/api", () => ({
  api: { post: jest.fn(), get: jest.fn() },
}));
const saved = {
  id: "flow",
  user_id: "owner",
  name: "flow",
  version_token: "draft-1",
  data: { nodes: [], edges: [] },
  access_type: "PUBLIC",
};
const unpublished = {
  status: "unpublished",
  publication_token: null,
  current: null,
  has_unpublished_changes: false,
  latest_version: 0,
};
const published = {
  status: "published",
  publication_token: "state-1",
  latest_version: 1,
  has_unpublished_changes: false,
  current: {
    id: "release-1",
    version_number: 1,
    created_at: "2026-11-08T00:00:00Z",
  },
};
beforeEach(() => {
  jest.resetAllMocks();
  useAuthStore.setState({
    userData: { id: "owner" } as never,
    platformWorkflowActions: [
      "read",
      "update",
      "publish",
      "share",
      "api_access",
    ],
  });
  useFlowsManagerStore.setState({
    currentFlow: saved as never,
    currentFlowId: saved.id,
  });
  useFlowStore.setState({
    currentFlow: saved as never,
    nodes: [],
    edges: [],
    userEditedSinceLoad: false,
  });
  (api.get as jest.Mock).mockImplementation((url: string) =>
    Promise.resolve({
      data: url.endsWith("/publications")
        ? { items: [], total: 0 }
        : unpublished,
    }),
  );
  mockEnqueue.mockResolvedValue(undefined);
});
afterEach(() =>
  useAuthStore.setState({ userData: null, platformWorkflowActions: null }),
);

it("does not treat the legacy PUBLIC flag as a published version", async () => {
  render(<PlatformAccessMenu />);
  fireEvent.click(screen.getByTestId("platform-access-menu-button"));
  expect(await screen.findByText("未发布")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "保存并发布" })).toBeEnabled();
  expect(
    screen.queryByRole("button", { name: "下线" }),
  ).not.toBeInTheDocument();
});

it("shows unpublished changes for an unsaved canvas while the saved draft still matches online", async () => {
  useFlowStore.setState({
    userEditedSinceLoad: true,
    currentFlow: {
      ...saved,
      data: { nodes: [{ id: "unsaved-node" }], edges: [] },
    } as never,
    nodes: [{ id: "unsaved-node" }] as never,
  });
  (api.get as jest.Mock).mockImplementation((url: string) =>
    Promise.resolve({
      data: url.endsWith("/publications") ? { items: [], total: 0 } : published,
    }),
  );
  render(<PlatformAccessMenu />);
  fireEvent.click(screen.getByTestId("platform-access-menu-button"));
  await screen.findByText("线上版本 V1");
  expect(screen.getByText("有未发布修改")).toBeInTheDocument();
});

it("publishes the revision adopted after saving and displays the resulting online version", async () => {
  mockEnqueue.mockImplementation(async () => {
    useFlowsManagerStore.setState({
      currentFlow: { ...saved, version_token: "draft-2" } as never,
    });
  });
  (api.post as jest.Mock).mockResolvedValue({ data: published });
  render(<PlatformAccessMenu />);
  fireEvent.click(screen.getByTestId("platform-access-menu-button"));
  await screen.findByText("未发布");
  await act(async () =>
    fireEvent.click(screen.getByRole("button", { name: "保存并发布" })),
  );
  expect(api.post).toHaveBeenCalledWith(
    expect.stringContaining("/flows/flow/publish"),
    { published: true, publication_token: null },
    expect.objectContaining({ headers: { "If-Match": "draft-2" } }),
  );
  expect(await screen.findByText("线上版本 V1")).toBeInTheDocument();
});

it("keeps the online version and reports a save failure without publishing", async () => {
  (api.get as jest.Mock).mockImplementation((url: string) =>
    Promise.resolve({
      data: url.endsWith("/publications") ? { items: [], total: 0 } : published,
    }),
  );
  mockEnqueue.mockRejectedValue(new Error("保存失败"));
  render(<PlatformAccessMenu />);
  fireEvent.click(screen.getByTestId("platform-access-menu-button"));
  await screen.findByText("线上版本 V1");
  await act(async () =>
    fireEvent.click(screen.getByRole("button", { name: "保存并发布" })),
  );
  expect(screen.getByRole("alert")).toHaveTextContent("保存失败");
  expect(screen.getByText("线上版本 V1")).toBeInTheDocument();
  expect(api.post).not.toHaveBeenCalled();
});

it("requires confirmation before taking an online workflow offline", async () => {
  (api.get as jest.Mock).mockImplementation((url: string) =>
    Promise.resolve({
      data: url.endsWith("/publications") ? { items: [], total: 0 } : published,
    }),
  );
  (api.post as jest.Mock).mockResolvedValue({
    data: { ...published, status: "offline", current: null },
  });
  render(<PlatformAccessMenu />);
  fireEvent.click(screen.getByTestId("platform-access-menu-button"));
  await screen.findByText("线上版本 V1");
  fireEvent.click(screen.getByRole("button", { name: "下线" }));
  expect(api.post).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "确认下线" }));
  await waitFor(() => expect(screen.getByText("已下线")).toBeInTheDocument());
});

it("runs the published snapshot while the draft is dirty and retains its result after draft saves", async () => {
  useAuthStore.setState({ platformWorkflowActions: ["read", "run"] });
  useFlowStore.setState({
    userEditedSinceLoad: true,
    nodes: [{ id: "draft-node" }] as never,
  });
  (api.get as jest.Mock).mockResolvedValue({ data: published });
  (api.post as jest.Mock).mockImplementation(async (_url, body) => ({
    data: {
      status: "succeeded",
      run_id: body.run_id,
      flow_id: saved.id,
      version_token: "release-1",
      publication_id: "release-1",
      publication_version: 1,
      outputs: [{ node_id: "out", text: "线上 V1 的结果" }],
    },
  }));
  render(<PlatformDryRunButton action="run" />);
  fireEvent.click(screen.getByTestId("platform-run-button"));
  const button = await screen.findByRole("button", { name: "开始运行" });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
  expect(await screen.findByText("线上 V1 的结果")).toBeInTheDocument();
  act(() =>
    useFlowsManagerStore.setState({
      currentFlow: { ...saved, version_token: "new-draft" } as never,
    }),
  );
  expect(screen.getByText("线上 V1 的结果")).toBeInTheDocument();
});

it("reports an accepted run ID without claiming the online version changed", async () => {
  useAuthStore.setState({ platformWorkflowActions: ["read", "run"] });
  (api.get as jest.Mock).mockResolvedValue({ data: published });
  (api.post as jest.Mock).mockRejectedValue({
    response: {
      status: 409,
      data: {
        detail: { code: "workflow_run_accepted", message: "运行 ID 已受理" },
      },
    },
  });
  render(<PlatformDryRunButton action="run" />);
  fireEvent.click(screen.getByTestId("platform-run-button"));
  const button = await screen.findByRole("button", { name: "开始运行" });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
  expect(await screen.findByRole("alert")).toHaveTextContent("运行 ID 已受理");
  expect(screen.queryByText(/服务端流程版本已变化/)).not.toBeInTheDocument();
  expect(api.post).toHaveBeenCalledTimes(1);
});

it("confirms rollback and keeps the online version on a concurrent publication conflict", async () => {
  const previous = {
    ...published.current,
    id: "release-old",
    version_number: 1,
  };
  const current = {
    ...published,
    current: { ...published.current, version_number: 2 },
  };
  (api.get as jest.Mock).mockImplementation((url: string) =>
    Promise.resolve({
      data: url.endsWith("/publications")
        ? { items: [current.current, previous], total: 2 }
        : current,
    }),
  );
  (api.post as jest.Mock).mockRejectedValue({
    response: { status: 409, data: { detail: "线上版本已变化" } },
  });
  render(<PlatformAccessMenu />);
  fireEvent.click(screen.getByTestId("platform-access-menu-button"));
  await screen.findByText("线上版本 V2");
  fireEvent.click(screen.getByRole("button", { name: "回滚至 V1" }));
  expect(api.post).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "确认回滚" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("线上版本已变化");
  expect(screen.getByText("线上版本 V2")).toBeInTheDocument();
  expect(api.post).toHaveBeenCalledWith(
    expect.stringContaining("/publications/release-old/activate"),
    { publication_token: "state-1" },
  );
  expect(mockEnqueue).not.toHaveBeenCalled();
});

it("keeps a successful publication visible when reading history fails", async () => {
  (api.post as jest.Mock).mockResolvedValue({ data: published });
  render(<PlatformAccessMenu />);
  fireEvent.click(screen.getByTestId("platform-access-menu-button"));
  await screen.findByText("未发布");
  (api.get as jest.Mock).mockRejectedValue(new Error("history unavailable"));
  fireEvent.click(screen.getByRole("button", { name: "保存并发布" }));
  expect(await screen.findByText("线上版本 V1")).toBeInTheDocument();
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "操作已成功，发布历史加载失败",
  );
});
