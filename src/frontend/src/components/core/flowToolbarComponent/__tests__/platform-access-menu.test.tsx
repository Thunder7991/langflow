import { act, fireEvent, render, screen } from "@testing-library/react";
import PlatformAccessMenu from "../components/platform-access-menu";

let mockAuth = {
  userData: { id: "owner" },
  platformWorkflowActions: ["read", "publish", "share", "api_access"],
};
const mockFlow = {
  id: "flow",
  user_id: "owner",
  access_type: "PRIVATE",
  version_token: "draft",
  name: "flow",
  data: { nodes: [], edges: [] },
};
const mockPost = jest.fn();
const mockGet = jest.fn();
jest.mock("@/stores/authStore", () => ({
  __esModule: true,
  default: Object.assign(
    (selector: (state: typeof mockAuth) => unknown) => selector(mockAuth),
    { getState: () => mockAuth },
  ),
}));
jest.mock("@/stores/flowsManagerStore", () => ({
  __esModule: true,
  default: Object.assign(
    (selector: (state: { currentFlow: typeof mockFlow }) => unknown) =>
      selector({ currentFlow: mockFlow }),
    { getState: () => ({ currentFlow: mockFlow }) },
  ),
}));
jest.mock("@/hooks/flows/use-autosave-flow", () => ({
  __esModule: true,
  default: () => ({ enqueue: async () => undefined }),
}));
jest.mock("@/stores/flowStore", () => ({
  __esModule: true,
  default: Object.assign(
    (selector: (state: unknown) => unknown) =>
      selector({
        currentFlow: mockFlow,
        nodes: [],
        edges: [],
        userEditedSinceLoad: false,
      }),
    {
      getState: () => ({
        currentFlow: mockFlow,
        nodes: [],
        edges: [],
        userEditedSinceLoad: false,
      }),
    },
  ),
}));
jest.mock("@/controllers/API/api", () => ({
  api: {
    post: (...args: unknown[]) => mockPost(...args),
    get: (...args: unknown[]) => mockGet(...args),
  },
}));
jest.mock("@/controllers/API/helpers/constants", () => ({
  getURL: () => "/api/v2/platform-workflow",
}));

beforeEach(() => {
  jest.clearAllMocks();
  mockAuth = {
    userData: { id: "owner" },
    platformWorkflowActions: ["read", "publish", "share", "api_access"],
  };
});

it("offers only granted access actions and displays the authenticated link", async () => {
  mockAuth.platformWorkflowActions = ["read", "share"];
  mockGet.mockImplementation((url: string) =>
    Promise.resolve({
      data: url.endsWith("/publication")
        ? { status: "unpublished", current: null }
        : url.endsWith("/publications")
          ? { items: [], total: 0 }
          : { url: "https://workflow.example/flow/flow" },
    }),
  );
  render(<PlatformAccessMenu />);
  fireEvent.click(screen.getByTestId("platform-access-menu-button"));
  expect(screen.queryByText("保存并发布")).not.toBeInTheDocument();
  expect(screen.queryByText("查看 API 调用配置")).not.toBeInTheDocument();
  await act(async () => fireEvent.click(screen.getByText("获取工作流链接")));
  expect(
    screen.getByText("https://workflow.example/flow/flow"),
  ).toBeInTheDocument();
  expect(mockPost).not.toHaveBeenCalled();
});

it("hides actions when none are granted", () => {
  mockAuth.platformWorkflowActions = ["read"];
  render(<PlatformAccessMenu />);
  expect(
    screen.queryByTestId("platform-access-menu-button"),
  ).not.toBeInTheDocument();
  expect(mockGet).not.toHaveBeenCalled();
});

it("discards an access response after identity changes", async () => {
  let finish!: (response: { data: { url: string } }) => void;
  mockGet.mockReturnValue(
    new Promise((resolve) => {
      finish = resolve;
    }),
  );
  const view = render(<PlatformAccessMenu />);
  fireEvent.click(screen.getByTestId("platform-access-menu-button"));
  fireEvent.click(screen.getByText("获取工作流链接"));
  mockAuth = { ...mockAuth, userData: { id: "other" } };
  view.rerender(<PlatformAccessMenu />);
  await act(async () => finish({ data: { url: "old-private-link" } }));
  expect(screen.queryByText("old-private-link")).not.toBeInTheDocument();
});
