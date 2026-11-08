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
import PlaygroundButton from "../components/playground-button";

jest.mock("@/controllers/API/api", () => ({
  api: { post: jest.fn(), get: jest.fn() },
}));
jest.mock("@/contexts/permissionsContext", () => ({
  usePermissions: () => ({ can: () => false }),
}));
jest.mock("@/components/common/genericIconComponent", () => ({
  __esModule: true,
  default: () => null,
}));
const graph = { nodes: [], edges: [] };
const saved = {
  id: "saved-flow",
  user_id: "owner",
  version_token: "saved-version",
  name: "saved",
  data: graph,
};
beforeEach(() => {
  jest.clearAllMocks();
  (api.get as jest.Mock).mockResolvedValue({
    data: {
      status: "published",
      current: { id: "published-version", version_number: 1 },
    },
  });
  useAuthStore.setState({
    userData: { id: "owner" } as never,
    platformWorkflowActions: ["read", "dry_run"],
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
});
afterEach(() =>
  useAuthStore.setState({ userData: null, platformWorkflowActions: null }),
);

it("runs and stops through independent platform actions using the same run ID", async () => {
  useAuthStore.setState({ platformWorkflowActions: ["read", "run", "stop"] });
  let finish!: (value: unknown) => void;
  (api.post as jest.Mock).mockImplementation((url: string) =>
    url.endsWith("/stop")
      ? Promise.resolve({ data: { status: "cancel_requested" } })
      : new Promise((resolve) => {
          finish = resolve;
        }),
  );
  const view = render(<PlaygroundButton hasIO />);
  expect(
    screen.queryByRole("button", { name: "纯文本调试" }),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "工作流运行" }));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "开始运行" })).toBeEnabled(),
  );
  fireEvent.click(screen.getByRole("button", { name: "开始运行" }));
  await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
  const runId = (api.post as jest.Mock).mock.calls[0][1].run_id;
  expect(runId).toMatch(/^[a-f0-9-]{36}$/);
  expect((api.post as jest.Mock).mock.calls[0][0]).toContain(
    `/platform-workflow/flows/${saved.id}/run`,
  );
  fireEvent.click(screen.getByRole("button", { name: "停止服务端运行" }));
  await waitFor(() =>
    expect((api.post as jest.Mock).mock.calls[1][0]).toContain(
      `/runs/${runId}/stop`,
    ),
  );
  await act(async () =>
    finish({ data: { status: "cancelled", run_id: runId, outputs: [] } }),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent("已取消");
  view.unmount();
});

it("runs the saved version with no posted graph and renders escaped output", async () => {
  (api.post as jest.Mock).mockResolvedValue({
    data: {
      status: "succeeded",
      run_id: "run-one",
      flow_id: saved.id,
      version_token: saved.version_token,
      outputs: [{ node_id: "out", text: "<b>result</b>" }],
    },
  });
  const view = render(<PlaygroundButton hasIO />);
  fireEvent.click(screen.getByRole("button", { name: "纯文本调试" }));
  fireEvent.click(screen.getByRole("button", { name: "运行已保存版本" }));
  expect(await screen.findByText("<b>result</b>")).toBeInTheDocument();
  expect(screen.queryByText("result")).not.toBeInTheDocument();
  expect(api.post).toHaveBeenCalledWith(
    expect.stringContaining(`/platform-flows/${saved.id}/dry-run`),
    {},
    expect.objectContaining({ headers: { "If-Match": saved.version_token } }),
  );
  expect(useFlowStore.getState().currentFlow).toEqual(saved);
  view.unmount();
});

it("does not derive run permission from editing and prevents unsaved graph execution", async () => {
  useAuthStore.setState({ platformWorkflowActions: ["read", "update"] });
  const view = render(<PlaygroundButton hasIO />);
  expect(
    screen.queryByRole("button", { name: "纯文本调试" }),
  ).not.toBeInTheDocument();
  act(() =>
    useAuthStore.setState({ platformWorkflowActions: ["read", "dry_run"] }),
  );
  fireEvent.click(screen.getByRole("button", { name: "纯文本调试" }));
  act(() =>
    useFlowStore.setState({
      userEditedSinceLoad: true,
      currentFlow: {
        ...saved,
        data: { nodes: [{ id: "draft" }], edges: [] },
      } as never,
      nodes: [{ id: "draft" }] as never,
    }),
  );
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "运行已保存版本" }),
    ).toBeDisabled(),
  );
  expect(api.post).not.toHaveBeenCalled();
  view.unmount();
});

it("aborts waiting and rejects a late result after run permission is revoked", async () => {
  let finish!: (value: unknown) => void;
  (api.post as jest.Mock).mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const view = render(<PlaygroundButton hasIO />);
  fireEvent.click(screen.getByRole("button", { name: "纯文本调试" }));
  fireEvent.click(screen.getByRole("button", { name: "运行已保存版本" }));
  const signal = (api.post as jest.Mock).mock.calls[0][2].signal as AbortSignal;
  act(() => useAuthStore.setState({ platformWorkflowActions: ["read"] }));
  await waitFor(() => expect(signal.aborted).toBe(true));
  await act(async () =>
    finish({
      data: { status: "succeeded", outputs: [{ text: "late private result" }] },
    }),
  );
  expect(screen.queryByText("late private result")).not.toBeInTheDocument();
  view.unmount();
});

it("shows a failed response without a fabricated success or clearing the saved canvas", async () => {
  (api.post as jest.Mock).mockRejectedValue({
    platformWorkflow: true,
    response: { status: 403, data: { detail: "revoked" } },
  });
  const view = render(<PlaygroundButton hasIO />);
  fireEvent.click(screen.getByRole("button", { name: "纯文本调试" }));
  fireEvent.click(screen.getByRole("button", { name: "运行已保存版本" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("权限");
  expect(screen.queryByText("运行成功")).not.toBeInTheDocument();
  expect(useFlowStore.getState().currentFlow).toEqual(saved);
  view.unmount();
});

it("requires a known owner and read access even when dry_run is present", () => {
  useAuthStore.setState({ platformWorkflowActions: ["dry_run"] });
  const view = render(<PlaygroundButton hasIO />);
  expect(
    screen.queryByRole("button", { name: "纯文本调试" }),
  ).not.toBeInTheDocument();
  act(() => {
    useAuthStore.setState({
      userData: null,
      platformWorkflowActions: ["read", "dry_run"],
    });
    useFlowsManagerStore.setState({
      currentFlow: { ...saved, user_id: undefined } as never,
    });
  });
  expect(screen.getByRole("button", { name: "纯文本调试" })).toBeDisabled();
  expect(api.post).not.toHaveBeenCalled();
  view.unmount();
});

it("distinguishes an already accepted model run from a saved-version conflict", async () => {
  (api.post as jest.Mock).mockRejectedValue({
    response: {
      status: 409,
      data: { detail: { code: "platform_model_run_accepted" } },
    },
  });
  const view = render(<PlaygroundButton hasIO />);
  fireEvent.click(screen.getByRole("button", { name: "纯文本调试" }));
  fireEvent.click(screen.getByRole("button", { name: "运行已保存版本" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "本次模型运行已受理",
  );
  expect(screen.queryByText(/服务端流程版本已变化/)).not.toBeInTheDocument();
  expect(useFlowStore.getState().currentFlow).toEqual(saved);
  view.unmount();
});

it("runs a saved platform model graph without sending credentials or model overrides", async () => {
  const nodes = [
    {
      id: "platform-model",
      type: "genericNode",
      data: { type: "PlatformModel" },
    },
  ];
  const modelFlow = { ...saved, data: { nodes, edges: [] } };
  useFlowsManagerStore.setState({ currentFlow: modelFlow as never });
  useFlowStore.setState({
    currentFlow: modelFlow as never,
    nodes: nodes as never,
  });
  (api.post as jest.Mock).mockResolvedValue({
    data: {
      status: "succeeded",
      run_id: "model-run",
      flow_id: saved.id,
      version_token: saved.version_token,
      outputs: [{ node_id: "out", text: "平台模型输出" }],
    },
  });
  const view = render(<PlaygroundButton hasIO />);
  fireEvent.click(screen.getByRole("button", { name: "平台模型调试" }));
  fireEvent.click(screen.getByRole("button", { name: "运行已保存版本" }));
  expect(await screen.findByText("平台模型输出")).toBeInTheDocument();
  expect(api.post).toHaveBeenCalledWith(
    expect.stringContaining("/dry-run"),
    {},
    expect.objectContaining({ timeout: 60000 }),
  );
  expect(useFlowStore.getState().currentFlow).toEqual(modelFlow);
  view.unmount();
});

it("warns that an uncertain model failure can still be billed", async () => {
  const nodes = [{ id: "model", data: { type: "PlatformModel" } }];
  const modelFlow = { ...saved, data: { nodes, edges: [] } };
  useFlowsManagerStore.setState({ currentFlow: modelFlow as never });
  useFlowStore.setState({
    currentFlow: modelFlow as never,
    nodes: nodes as never,
  });
  (api.post as jest.Mock).mockRejectedValue({ response: { status: 502 } });
  const view = render(<PlaygroundButton hasIO />);
  fireEvent.click(screen.getByRole("button", { name: "平台模型调试" }));
  fireEvent.click(screen.getByRole("button", { name: "运行已保存版本" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "重复运行可能再次计费",
  );
  expect(api.post).toHaveBeenCalledTimes(1);
  view.unmount();
});

it.each([
  ["TENANT_DEFAULT_UNSET", "企业默认模型未设置。"],
  ["MODEL_UNAVAILABLE", "企业模型或所属渠道不可用。"],
])(
  "reports catalog state %s separately from a flow version conflict",
  async (code, message) => {
    (api.post as jest.Mock).mockRejectedValue({
      response: { status: 409, data: { detail: { code, message } } },
    });
    const view = render(<PlaygroundButton hasIO />);
    fireEvent.click(screen.getByRole("button", { name: "纯文本调试" }));
    fireEvent.click(screen.getByRole("button", { name: "运行已保存版本" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.queryByText(/服务端流程版本已变化/)).not.toBeInTheDocument();
    expect(api.post).toHaveBeenCalledTimes(1);
    view.unmount();
  },
);

it("explains a discarded response when the saved revision changes before the rendered selector catches up", async () => {
  (api.post as jest.Mock).mockResolvedValue({
    data: {
      status: "succeeded",
      flow_id: saved.id,
      version_token: saved.version_token,
      outputs: [{ node_id: "out", text: "outdated private result" }],
    },
  });
  const view = render(<PlaygroundButton hasIO />);
  fireEvent.click(screen.getByRole("button", { name: "纯文本调试" }));
  const state = useFlowsManagerStore.getState();
  const latest = jest.spyOn(useFlowsManagerStore, "getState").mockReturnValue({
    ...state,
    currentFlow: { ...saved, version_token: "new-revision" } as never,
  });
  fireEvent.click(screen.getByRole("button", { name: "运行已保存版本" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("草稿版本已变化");
  expect(screen.queryByText("outdated private result")).not.toBeInTheDocument();
  latest.mockRestore();
  view.unmount();
});
