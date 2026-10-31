import { act, fireEvent, render, screen } from "@testing-library/react";
import useAuthStore from "@/stores/authStore";
import useFlowStore from "@/stores/flowStore";
import { usePlaygroundStore } from "@/stores/playgroundStore";
import { useShortcutsStore } from "@/stores/shortcuts";
import FlowToolbar from "..";

jest.mock("@xyflow/react", () => ({
  Panel: ({ children }) => <div>{children}</div>,
}));
jest.mock("../components/flow-toolbar-options", () => ({
  __esModule: true,
  default: () => null,
}));
jest.mock("@/modals/exportModal", () => ({
  __esModule: true,
  default: ({ open }) => (open ? <div role="dialog">Export</div> : null),
}));

beforeEach(() => {
  useAuthStore.setState({
    platformWorkflowActions: ["read", "create", "update"],
  });
  useFlowStore.setState({ hasIO: true });
  usePlaygroundStore.setState({ isOpen: false });
  useShortcutsStore.setState({ openPlayground: "p", api: "a", flow: "e" });
});
afterEach(() => useAuthStore.setState({ platformWorkflowActions: null }));

it("blocks execution and export keyboard shortcuts under platform editing permissions", () => {
  render(<FlowToolbar />);
  fireEvent.keyDown(document.body, { key: "p", code: "KeyP" });
  expect(usePlaygroundStore.getState().isOpen).toBe(false);
  fireEvent.keyDown(document.body, { key: "e", code: "KeyE" });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("closes an already-open playground after platform capabilities arrive", () => {
  useAuthStore.setState({ platformWorkflowActions: null });
  usePlaygroundStore.setState({ isOpen: true });
  render(<FlowToolbar />);
  act(() => useAuthStore.setState({ platformWorkflowActions: ["read"] }));
  expect(usePlaygroundStore.getState().isOpen).toBe(false);
});
