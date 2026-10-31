import { fireEvent, renderHook } from "@testing-library/react";
import useAuthStore from "@/stores/authStore";
import { useShortcutsStore } from "@/stores/shortcuts";
import useShortcuts from "../use-shortcuts";

beforeEach(() => {
  useAuthStore.setState({ platformWorkflowActions: ["read", "update"] });
  useShortcutsStore.setState({
    download: "d",
    saveComponent: "s",
    componentShare: "h",
    freezePath: "f",
    code: "c",
  });
});
afterEach(() => useAuthStore.setState({ platformWorkflowActions: null }));

it("blocks node export, saved-component, freeze, share and code shortcuts in platform mode", () => {
  const unavailableAction = jest.fn();
  renderHook(() =>
    useShortcuts({
      downloadFunction: unavailableAction,
      saveComponent: unavailableAction,
      FreezeAllVertices: unavailableAction,
      shareComponent: unavailableAction,
      handleCodeModal: unavailableAction,
    }),
  );
  for (const key of ["d", "s", "h", "f", "c"])
    fireEvent.keyDown(document.body, { key, code: `Key${key.toUpperCase()}` });
  expect(unavailableAction).not.toHaveBeenCalled();
});
