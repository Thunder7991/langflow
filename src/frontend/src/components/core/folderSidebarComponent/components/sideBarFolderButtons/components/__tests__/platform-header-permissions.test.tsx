import { fireEvent, render, screen } from "@testing-library/react";
import useAuthStore from "@/stores/authStore";
import { useUtilityStore } from "@/stores/utilityStore";
import { HeaderButtons } from "../header-buttons";

jest.mock("@/controllers/API/queries/auth", () => ({
  useUpdateUser: () => ({ mutate: jest.fn() }),
}));
jest.mock("@/components/common/genericIconComponent", () => ({
  __esModule: true,
  default: () => null,
}));
jest.mock("@/components/ui/sidebar", () => ({ SidebarTrigger: () => null }));
jest.mock(
  "@/customization/components/custom-get-started-progress",
  () => () => null,
);

afterEach(() => useAuthStore.setState({ platformWorkflowActions: null }));

it.each([
  { actions: ["read"], create: false, upload: false },
  { actions: ["read", "create"], create: true, upload: false },
  { actions: ["read", "update"], create: false, upload: false },
  { actions: ["read", "create", "update"], create: true, upload: false },
  { actions: null, create: true, upload: true },
])(
  "gates project creation and import for $actions",
  ({ actions, create, upload }) => {
    useAuthStore.setState({ platformWorkflowActions: actions });
    useUtilityStore.setState({ hideNewProjectButton: false });
    const add = jest.fn();
    const importFlow = jest.fn();
    render(
      <HeaderButtons
        addNewFolder={add}
        handleUploadFlowsToFolder={importFlow}
        isPending={false}
        isUpdatingFolder={false}
      />,
    );
    const createButton = screen.getByTestId("add-project-button");
    const uploadButton = screen.getByTestId("upload-project-button");
    expect(createButton).toHaveProperty("disabled", !create);
    expect(uploadButton).toHaveProperty("disabled", !upload);
    fireEvent.click(createButton);
    fireEvent.click(uploadButton);
    expect(add).toHaveBeenCalledTimes(create ? 1 : 0);
    expect(importFlow).toHaveBeenCalledTimes(upload ? 1 : 0);
  },
);
