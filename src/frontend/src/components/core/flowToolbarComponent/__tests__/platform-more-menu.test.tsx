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
import PlatformMoreMenu from "../components/platform-more-menu";

jest.mock("@/controllers/API/api", () => ({
  api: { post: jest.fn(), get: jest.fn() },
}));

const saved = {
  id: "flow",
  user_id: "owner",
  name: "flow",
  version_token: "draft-1",
  data: { nodes: [], edges: [] },
};

beforeEach(() => {
  jest.clearAllMocks();
  useAuthStore.setState({
    userData: { id: "owner" } as never,
    platformWorkflowActions: ["read", "dry_run", "run"],
  });
  useFlowsManagerStore.setState({ currentFlow: saved as never });
  useFlowStore.setState({
    currentFlow: saved as never,
    nodes: [],
    edges: [],
    userEditedSinceLoad: false,
  });
  (api.get as jest.Mock).mockResolvedValue({
    data: { current: { id: "release-1", version_number: 1 } },
  });
});

afterEach(() =>
  useAuthStore.setState({ userData: null, platformWorkflowActions: null }),
);

const openMore = async () => {
  fireEvent.keyDown(screen.getByRole("button", { name: "更多" }), {
    key: "ArrowDown",
    code: "ArrowDown",
  });
  await screen.findByRole("menu");
};

it("opens the draft dialog from More and keeps it mounted after the menu closes", async () => {
  render(<PlatformMoreMenu />);
  expect(
    screen.queryByRole("button", { name: "纯文本调试" }),
  ).not.toBeInTheDocument();
  await openMore();
  fireEvent.click(screen.getByRole("menuitem", { name: "纯文本调试" }));
  expect(
    await screen.findByRole("dialog", { name: "纯文本调试" }),
  ).toBeVisible();
  expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  expect(api.post).not.toHaveBeenCalled();
  (api.post as jest.Mock).mockResolvedValue({
    data: {
      status: "succeeded",
      flow_id: "flow",
      version_token: "draft-1",
      outputs: [{ node_id: "output", text: "草稿结果" }],
    },
  });
  fireEvent.click(screen.getByRole("button", { name: "运行已保存版本" }));
  expect(await screen.findByText("草稿结果")).toBeVisible();
});

it("returns keyboard focus to More when its dialog closes", async () => {
  render(<PlatformMoreMenu />);
  await openMore();
  fireEvent.click(screen.getByRole("menuitem", { name: "纯文本调试" }));
  await screen.findByRole("dialog", { name: "纯文本调试" });
  fireEvent.keyDown(document, { key: "Escape", code: "Escape" });
  await waitFor(() =>
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
  );
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "更多" })).toHaveFocus(),
  );
});

it("opens the published dialog from More and runs its publication version", async () => {
  render(<PlatformMoreMenu />);
  await openMore();
  fireEvent.click(screen.getByRole("menuitem", { name: "工作流运行" }));
  expect(
    await screen.findByRole("dialog", { name: "工作流运行" }),
  ).toBeVisible();
  expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "开始运行" })).toBeEnabled(),
  );
  (api.post as jest.Mock).mockImplementation((_url, body) =>
    Promise.resolve({
      data: {
        status: "succeeded",
        run_id: body.run_id,
        flow_id: "flow",
        version_token: "release-1",
        publication_id: "release-1",
        publication_version: 1,
        outputs: [{ node_id: "output", text: "发布结果" }],
      },
    }),
  );
  fireEvent.click(screen.getByRole("button", { name: "开始运行" }));
  expect(await screen.findByText("发布结果")).toBeVisible();
  expect(api.post).toHaveBeenCalledWith(
    expect.stringContaining("/platform-workflow/flows/flow/run"),
    expect.objectContaining({ run_id: expect.any(String) }),
    expect.objectContaining({ headers: { "If-Match": "release-1" } }),
  );
});

it.each([[null], [["read"]], [["read", "update"]], [["dry_run", "run"]]])(
  "does not offer More without platform read and execution permissions: %s",
  (actions) => {
    useAuthStore.setState({ platformWorkflowActions: actions });
    render(<PlatformMoreMenu />);
    expect(
      screen.queryByRole("button", { name: "更多" }),
    ).not.toBeInTheDocument();
  },
);

it.each([
  ["dry_run", "纯文本调试", "工作流运行"],
  ["run", "工作流运行", "纯文本调试"],
])(
  "offers only the permitted %s action",
  async (action, available, unavailable) => {
    useAuthStore.setState({ platformWorkflowActions: ["read", action] });
    render(<PlatformMoreMenu />);
    await openMore();
    expect(screen.getByRole("menuitem", { name: available })).toBeVisible();
    expect(
      screen.queryByRole("menuitem", { name: unavailable }),
    ).not.toBeInTheDocument();
  },
);

it("prevents opening a run for an unknown flow owner", async () => {
  useAuthStore.setState({ userData: null });
  render(<PlatformMoreMenu />);
  await openMore();
  expect(screen.getByRole("menuitem", { name: "纯文本调试" })).toHaveAttribute(
    "data-disabled",
  );
  expect(screen.getByRole("menuitem", { name: "工作流运行" })).toHaveAttribute(
    "data-disabled",
  );
});

it("aborts waiting and hides a late result when More permissions are revoked", async () => {
  let finish!: (value: unknown) => void;
  (api.post as jest.Mock).mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  render(<PlatformMoreMenu />);
  await openMore();
  fireEvent.click(screen.getByRole("menuitem", { name: "纯文本调试" }));
  fireEvent.click(
    await screen.findByRole("button", { name: "运行已保存版本" }),
  );
  const signal = (api.post as jest.Mock).mock.calls[0][2].signal as AbortSignal;
  act(() => useAuthStore.setState({ platformWorkflowActions: ["read"] }));
  await waitFor(() => expect(signal.aborted).toBe(true));
  await act(async () =>
    finish({
      data: { status: "succeeded", outputs: [{ text: "late result" }] },
    }),
  );
  expect(screen.queryByText("late result")).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "更多" }),
  ).not.toBeInTheDocument();
});
