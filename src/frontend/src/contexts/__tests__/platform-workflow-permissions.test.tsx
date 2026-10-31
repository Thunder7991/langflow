import { act, renderHook } from "@testing-library/react";
import useAuthStore from "@/stores/authStore";
import { PermissionsProvider, usePermissions } from "../permissionsContext";

let mockPermissionResult = {
  data: undefined as
    | undefined
    | {
        resource_type: "flow";
        permissions: Record<string, string[]>;
      },
  isLoading: false,
  isError: true,
};

jest.mock("@/controllers/API/queries/permissions", () => ({
  useGetEffectivePermissions: () => mockPermissionResult,
}));

beforeEach(() => {
  mockPermissionResult = { data: undefined, isLoading: false, isError: true };
});
afterEach(() => useAuthStore.setState({ platformWorkflowActions: null }));

it("closes editing when a failed permission refresh retains a cached write grant", () => {
  useAuthStore.setState({ platformWorkflowActions: ["read", "update"] });
  mockPermissionResult = {
    data: {
      resource_type: "flow",
      permissions: { "own-flow": ["read", "write"] },
    },
    isLoading: false,
    isError: false,
  };
  const { result, rerender } = renderHook(() => usePermissions(), {
    wrapper: ({ children }) => (
      <PermissionsProvider resourceType="flow" resourceIds={["own-flow"]}>
        {children}
      </PermissionsProvider>
    ),
  });
  expect(result.current.can("own-flow", "write")).toBe(true);
  mockPermissionResult = { ...mockPermissionResult, isError: true };
  rerender();
  expect(result.current.can("own-flow", "write")).toBe(false);
});

it("blocks delete and execute even without an enclosing permission provider", () => {
  useAuthStore.setState({
    platformWorkflowActions: ["read", "create", "update"],
  });
  const { result } = renderHook(() => usePermissions());
  expect(result.current.can(null, "create")).toBe(true);
  expect(result.current.can("own-flow", "write")).toBe(true);
  expect(result.current.can("own-flow", "delete")).toBe(false);
  expect(result.current.can("own-flow", "execute")).toBe(false);
});

it("does not fail open when the resource-permission query fails in platform mode", () => {
  useAuthStore.setState({ platformWorkflowActions: ["read", "update"] });
  const { result } = renderHook(() => usePermissions(), {
    wrapper: ({ children }) => (
      <PermissionsProvider resourceType="flow" resourceIds={["own-flow"]}>
        {children}
      </PermissionsProvider>
    ),
  });
  expect(result.current.can("own-flow", "write")).toBe(false);
});

it("reflects revoked editing actions without remounting the page", () => {
  useAuthStore.setState({
    platformWorkflowActions: ["read", "create", "update"],
  });
  const { result, rerender } = renderHook(() => usePermissions());
  act(() => useAuthStore.setState({ platformWorkflowActions: ["read"] }));
  rerender();
  expect(result.current.can(null, "create")).toBe(false);
  expect(result.current.can("own-flow", "write")).toBe(false);
});
