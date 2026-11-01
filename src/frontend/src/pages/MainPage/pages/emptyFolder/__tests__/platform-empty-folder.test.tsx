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
    const button = screen.queryByTestId("new_project_btn_empty_page");
    if (enabled) {
      expect(button).toBeEnabled();
      fireEvent.click(button!);
    } else expect(button).not.toBeInTheDocument();
    expect(create).toHaveBeenCalledTimes(enabled ? 1 : 0);
  },
);
