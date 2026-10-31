import { renderHook } from "@testing-library/react";
import useAuthStore from "@/stores/authStore";
import { PermissionsProvider, usePermissions } from "../permissionsContext";

jest.mock("@/controllers/API/queries/permissions", () => ({
  useGetEffectivePermissions: () => ({
    data: undefined,
    isLoading: false,
    isError: true,
  }),
}));

afterEach(() => useAuthStore.setState({ platformWorkflowActions: null }));

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
  useAuthStore.setState({ platformWorkflowActions: ["read"] });
  rerender();
  expect(result.current.can(null, "create")).toBe(false);
  expect(result.current.can("own-flow", "write")).toBe(false);
});
