import { fireEvent, render, screen } from "@testing-library/react";
import useAuthStore from "@/stores/authStore";
import EmptyFolder from "..";

jest.mock("@/components/common/genericIconComponent", () => ({
  __esModule: true,
  default: () => null,
}));
afterEach(() => useAuthStore.setState({ platformWorkflowActions: null }));

it.each([
  { actions: ["read"], enabled: false },
  { actions: ["read", "update"], enabled: false },
  { actions: ["read", "create"], enabled: true },
])(
  "keeps empty-project creation separate from update for $actions",
  ({ actions, enabled }) => {
    useAuthStore.setState({ platformWorkflowActions: actions });
    const create = jest.fn();
    render(<EmptyFolder setOpenModal={jest.fn()} onNewFlow={create} />);
    const button = screen.getByTestId("new_project_btn_empty_page");
    expect(button).toHaveProperty("disabled", !enabled);
    fireEvent.click(button);
    expect(create).toHaveBeenCalledTimes(enabled ? 1 : 0);
  },
);
