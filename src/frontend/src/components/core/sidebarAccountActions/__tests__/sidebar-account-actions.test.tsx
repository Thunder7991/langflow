import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SidebarProvider } from "@/components/ui/sidebar";
import useAlertStore from "@/stores/alertStore";
import SidebarAccountActions from "..";

const mockNavigate = jest.fn();

jest.mock("@/controllers/API/queries/auth", () => ({
  useLogout: () => ({ mutate: jest.fn() }),
}));
jest.mock("@/customization/hooks/use-custom-navigate", () => ({
  useCustomNavigate: () => mockNavigate,
}));
jest.mock("@/components/common/genericIconComponent", () => ({
  __esModule: true,
  default: () => null,
  ForwardedIconComponent: () => null,
}));

function renderActions() {
  return render(
    <SidebarProvider>
      <SidebarAccountActions />
    </SidebarProvider>,
  );
}

beforeEach(() => {
  mockNavigate.mockClear();
  useAlertStore.setState({ notificationList: [], notificationCenter: false });
});

it("opens the original account popup from Settings and preserves navigation", async () => {
  const user = userEvent.setup();
  renderActions();
  expect(screen.queryByTestId("user-profile-settings")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Settings" }));
  expect(screen.getByText("Version")).toBeInTheDocument();
  expect(screen.getByText("Theme")).toBeInTheDocument();
  await user.click(screen.getByTestId("menu_settings_button"));
  expect(mockNavigate).toHaveBeenCalledWith("/settings");
});

it("opens one notification popup with existing messages and clears unread state", () => {
  act(() => {
    useAlertStore.getState().addNotificationToHistory({
      title: "Existing workflow error",
      type: "error",
    });
  });
  renderActions();
  fireEvent.click(screen.getByTestId("notification_button"));
  expect(screen.getAllByTestId("notification-dropdown-content")).toHaveLength(
    1,
  );
  expect(screen.getByText("Existing workflow error")).toBeInTheDocument();
  expect(useAlertStore.getState().notificationCenter).toBe(false);
});
