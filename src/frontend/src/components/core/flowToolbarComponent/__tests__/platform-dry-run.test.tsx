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

jest.mock("@/controllers/API/api", () => ({ api: { post: jest.fn() } }));
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
  expect(screen.getByRole("button", { name: "纯文本调试" })).toBeDisabled();
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
  expect(screen.getByRole("button", { name: "纯文本调试" })).toBeDisabled();
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
