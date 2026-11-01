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
  { actions: ["read", "create"], create: false, upload: false },
  { actions: ["read", "project_create"], create: true, upload: false },
  { actions: ["read", "update"], create: false, upload: false },
  { actions: ["read", "create", "update"], create: false, upload: false },
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
    const createButton = screen.queryByTestId("add-project-button");
    const uploadButton = screen.queryByTestId("upload-project-button");
    if (create) fireEvent.click(createButton!);
    else expect(createButton).not.toBeInTheDocument();
    if (upload) fireEvent.click(uploadButton!);
    else expect(uploadButton).not.toBeInTheDocument();
    expect(add).toHaveBeenCalledTimes(create ? 1 : 0);
    expect(importFlow).toHaveBeenCalledTimes(upload ? 1 : 0);
  },
);
