import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { SimpleSidebarProvider } from "@/components/ui/simple-sidebar";
import { BuildStatus } from "@/constants/enums";
import { api } from "@/controllers/API/api";
import useAuthStore from "@/stores/authStore";
import useFlowStore from "@/stores/flowStore";
import useFlowsManagerStore from "@/stores/flowsManagerStore";
import { usePlaygroundStore } from "@/stores/playgroundStore";
import { PlatformPlayground } from "../platform-playground";
import {
  type PlaygroundEvent,
  streamPlatformPlayground,
} from "../platform-stream";

jest.mock("../platform-stream", () => ({
  streamPlatformPlayground: jest.fn(),
}));
jest.mock("@/controllers/API/api", () => ({
  api: { get: jest.fn(), post: jest.fn() },
}));
jest.mock("@/components/core/sanitizedMarkdown", () => ({
  SanitizedMarkdown: ({ chatMessage, isEmpty }) => (
    <div>{isEmpty ? "（空文本）" : chatMessage}</div>
  ),
}));
const nodes = ["in", "out"].map((id) => ({
  id,
  type: "genericNode",
  position: { x: 0, y: 0 },
  data: {
    type: id === "in" ? "TextInput" : "TextOutput",
    node: { template: {}, outputs: [{ name: "text" }] },
  },
}));
const saved = {
  id: "flow",
  name: "Example",
  user_id: "owner",
  version_token: "revision",
  data: { nodes, edges: [] },
};
let receive: (event: PlaygroundEvent) => void;
let complete: () => void;
function mount() {
  return render(
    <SimpleSidebarProvider defaultOpen>
      <PlatformPlayground isFullscreen={false} setIsFullscreen={jest.fn()} />
    </SimpleSidebarProvider>,
  );
}
function send() {
  fireEvent.change(screen.getByRole("textbox", { name: "聊天输入" }), {
    target: { value: "hello" },
  });
  fireEvent.click(screen.getByRole("button", { name: "发送" }));
}
beforeEach(() => {
  jest.clearAllMocks();
  useAuthStore.setState({
    userData: { id: "owner" } as never,
    platformWorkflowActions: ["read", "dry_run", "run", "stop"],
  });
  useFlowsManagerStore.setState({ currentFlow: saved as never });
  useFlowStore.setState({
    currentFlow: saved as never,
    nodes: nodes as never,
    edges: [],
    userEditedSinceLoad: false,
    flowBuildStatus: {},
    flowPool: {},
    isBuilding: false,
  });
  (api.get as jest.Mock).mockResolvedValue({
    data: {
      status: "published",
      current: { id: "release", version_number: 1 },
    },
  });
  (api.post as jest.Mock).mockResolvedValue({
    data: { status: "cancel_requested" },
  });
  (streamPlatformPlayground as jest.Mock).mockImplementation(
    (_options, callback) => {
      receive = callback;
      return new Promise<void>((resolve) => {
        complete = resolve;
      });
    },
  );
});
afterEach(() =>
  useAuthStore.setState({ platformWorkflowActions: null, userData: null }),
);
it("shows chat results and real canvas progress without changing the saved graph", async () => {
  mount();
  send();
  const request = (streamPlatformPlayground as jest.Mock).mock.calls[0][0];
  expect(request).toMatchObject({
    mode: "draft",
    input: "hello",
    version: "revision",
  });
  expect(request.data).toBeUndefined();
  act(() =>
    receive({ type: "node_started", run_id: request.runId, node_id: "in" }),
  );
  expect(useFlowStore.getState().flowBuildStatus.in.status).toBe(
    BuildStatus.BUILDING,
  );
  act(() =>
    receive({
      type: "node_finished",
      run_id: request.runId,
      node_id: "in",
      duration_ms: 2,
    }),
  );
  expect(useFlowStore.getState().flowBuildStatus.in.status).toBe(
    BuildStatus.BUILT,
  );
  await act(async () => {
    receive({
      type: "run_finished",
      run_id: request.runId,
      version_token: "revision",
      outputs: [{ node_id: "out", text: "answer" }],
    });
    complete();
  });
  expect(screen.getByText("answer")).toBeInTheDocument();
  expect(screen.getByText("执行过程")).toBeInTheDocument();
  expect(useFlowsManagerStore.getState().currentFlow?.data).toEqual(saved.data);
  expect(useFlowStore.getState().isBuilding).toBe(false);
});
it("blocks unsaved draft execution while allowing pinned published execution", async () => {
  mount();
  const changed = [
    {
      ...nodes[0],
      data: {
        ...nodes[0].data,
        node: { template: { input_value: { value: "changed" } } },
      },
    },
  ];
  act(() =>
    useFlowStore.setState({
      userEditedSinceLoad: true,
      nodes: changed as never,
      currentFlow: { ...saved, data: { nodes: changed, edges: [] } } as never,
    }),
  );
  expect(screen.getByRole("button", { name: "发送" })).toBeDisabled();
  fireEvent.change(screen.getByRole("combobox", { name: "执行版本" }), {
    target: { value: "published" },
  });
  await waitFor(() =>
    expect(screen.getByText(/线上版本 V1/)).toBeInTheDocument(),
  );
  send();
  expect(
    (streamPlatformPlayground as jest.Mock).mock.calls[0][0],
  ).toMatchObject({ mode: "published", version: "release" });
});
it("stops the same run and reports cancellation only after the server event", async () => {
  mount();
  send();
  const request = (streamPlatformPlayground as jest.Mock).mock.calls[0][0];
  fireEvent.click(screen.getByRole("button", { name: "停止运行" }));
  await waitFor(() =>
    expect(api.post).toHaveBeenCalledWith(
      expect.stringContaining(`/runs/${request.runId}/stop`),
      {},
    ),
  );
  expect(screen.queryByText("执行已取消")).not.toBeInTheDocument();
  await act(async () => {
    receive({ type: "run_cancelled", run_id: request.runId });
    complete();
  });
  expect(screen.getByText("执行已取消")).toBeInTheDocument();
});

it("routes the shared canvas stop action to the active Playground run and cleans the callback", async () => {
  const view = mount();
  send();
  const request = (streamPlatformPlayground as jest.Mock).mock.calls[0][0];
  expect(usePlaygroundStore.getState().requestPlatformStop).toEqual(
    expect.any(Function),
  );
  act(() => useFlowStore.getState().stopBuilding());
  await waitFor(() =>
    expect(api.post).toHaveBeenCalledWith(
      expect.stringContaining(`/runs/${request.runId}/stop`),
      {},
    ),
  );
  expect(useFlowStore.getState().isBuilding).toBe(true);
  view.unmount();
  expect(usePlaygroundStore.getState().requestPlatformStop).toBeNull();
});
it("clears private messages and aborts old execution when the identity or revision changes", async () => {
  mount();
  send();
  const request = (streamPlatformPlayground as jest.Mock).mock.calls[0][0];
  act(() => useAuthStore.setState({ userData: { id: "other" } as never }));
  expect(request.signal.aborted).toBe(true);
  await act(async () => {
    receive({
      type: "run_finished",
      run_id: request.runId,
      outputs: [{ node_id: "out", text: "private" }],
    });
    complete();
  });
  expect(screen.queryByText("private")).not.toBeInTheDocument();
  expect(screen.queryByText("hello")).not.toBeInTheDocument();
});

it("does not deliver a result if the saved revision changes before React processes the update", async () => {
  mount();
  send();
  const request = (streamPlatformPlayground as jest.Mock).mock.calls[0][0];
  await act(async () => {
    useFlowsManagerStore.setState({
      currentFlow: { ...saved, version_token: "new" } as never,
    });
    receive({
      type: "run_finished",
      run_id: request.runId,
      version_token: "revision",
      outputs: [{ node_id: "out", text: "stale result" }],
    });
    complete();
  });
  expect(screen.queryByText("stale result")).not.toBeInTheDocument();
  expect(useFlowStore.getState().flowPool).toEqual({});
});
