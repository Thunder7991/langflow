import type { APIClassType } from "@/types/api";

const mockApiPost = jest.fn();
const mockGetNode = jest.fn();
const mockFlowState: {
  currentFlowId: string | undefined;
  currentFlow: { folder_id?: string } | undefined;
} = {
  currentFlowId: "flow-one",
  currentFlow: { folder_id: "project-one" },
};

jest.mock("@/controllers/API/api", () => ({
  api: { post: mockApiPost },
}));

jest.mock("@/controllers/API/helpers/constants", () => ({
  getURL: jest.fn(() => "/api/v1/custom_component/update"),
}));

jest.mock("@/stores/flowsManagerStore", () => {
  const store = (selector: (state: typeof mockFlowState) => unknown) =>
    selector(mockFlowState);
  store.getState = () => mockFlowState;
  return { __esModule: true, default: store };
});

jest.mock("@/stores/flowStore", () => ({
  __esModule: true,
  default: (selector: (state: { getNode: typeof mockGetNode }) => unknown) =>
    selector({ getNode: mockGetNode }),
}));

jest.mock("@/stores/utilityStore", () => ({
  useUtilityStore: {
    getState: () => ({ allowCustomComponents: true }),
  },
}));

jest.mock("@/controllers/API/services/request-processor", () => ({
  UseRequestProcessor: jest.fn(() => ({
    mutate: jest.fn(
      (_key: unknown, mutationFn: (payload: unknown) => Promise<unknown>) => ({
        mutateAsync: mutationFn,
      }),
    ),
  })),
}));

import useAuthStore from "@/stores/authStore";
import { usePostTemplateValue } from "../use-post-template-value";

const node = {
  template: {
    code: { value: "class ScopedComponent: pass" },
    model: { value: "model-a" },
  },
} as unknown as APIClassType;

describe("usePostTemplateValue scope continuity", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockFlowState.currentFlowId = "flow-one";
    mockFlowState.currentFlow = { folder_id: "project-one" };
    mockGetNode.mockReturnValue({ data: { node } });
    useAuthStore.setState({ platformWorkflowActions: null });
  });

  afterEach(() => useAuthStore.setState({ platformWorkflowActions: null }));

  it.each([["read"], ["read", "create"], []])(
    "does not initialize a platform node without update: %j",
    async (...actions) => {
      useAuthStore.setState({ platformWorkflowActions: actions as string[] });
      const mutation = usePostTemplateValue({
        parameterId: "model",
        nodeId: "shared-node",
        node,
      });
      await expect(
        mutation.mutateAsync({ value: "model-b" }),
      ).resolves.toBeUndefined();
      expect(mockApiPost).not.toHaveBeenCalled();
    },
  );

  it("discards initialization after update is revoked while the request is pending", async () => {
    useAuthStore.setState({ platformWorkflowActions: ["read", "update"] });
    let complete!: (response: unknown) => void;
    mockApiPost.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          complete = resolve;
        }),
    );
    const mutation = usePostTemplateValue({
      parameterId: "model",
      nodeId: "shared-node",
      node,
    });
    const pending = mutation.mutateAsync({ value: "model-b" });
    useAuthStore.setState({ platformWorkflowActions: ["read"] });
    complete({ data: node });
    await expect(pending).resolves.toBeUndefined();
  });

  it("retains the selected class when refreshing a multi-class component", async () => {
    const selectedNode = {
      ...node,
      metadata: { component_class_name: "Second" },
    } as unknown as APIClassType;
    mockApiPost.mockResolvedValueOnce({ data: selectedNode });
    const mutation = usePostTemplateValue({
      parameterId: "model",
      nodeId: "shared-node",
      node: selectedNode,
    });

    await mutation.mutateAsync({ value: "model-b" });

    expect(mockApiPost).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({
        frontend_node: { metadata: { component_class_name: "Second" } },
      }),
    );
  });

  it("does not start a request after the captured flow scope becomes stale", async () => {
    const mutation = usePostTemplateValue({
      parameterId: "model",
      nodeId: "shared-node",
      node,
    });
    mockFlowState.currentFlowId = "flow-two";
    mockFlowState.currentFlow = { folder_id: "project-two" };

    await expect(mutation.mutateAsync({ value: "model-b" })).resolves.toBe(
      undefined,
    );
    expect(mockApiPost).not.toHaveBeenCalled();
  });

  it("discards a deferred response after switching to a same-id node in another scope", async () => {
    let resolveResponse: ((value: unknown) => void) | undefined;
    mockApiPost.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveResponse = resolve;
        }),
    );
    const mutation = usePostTemplateValue({
      parameterId: "model",
      nodeId: "shared-node",
      node,
    });

    const pending = mutation.mutateAsync({ value: "model-b" });
    await Promise.resolve();
    expect(mockApiPost).toHaveBeenCalledTimes(1);

    mockFlowState.currentFlowId = "flow-two";
    mockFlowState.currentFlow = { folder_id: "project-two" };
    resolveResponse?.({
      data: {
        ...node,
        template: {
          ...node.template,
          model: { value: "scope-a-model" },
        },
      },
    });

    await expect(pending).resolves.toBe(undefined);
  });
});
