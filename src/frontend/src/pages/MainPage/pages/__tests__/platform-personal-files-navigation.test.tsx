import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import useAuthStore from "@/stores/authStore";
import CollectionPage from "../main-page";

jest.mock("@tanstack/react-query", () => ({
  ...jest.requireActual("@tanstack/react-query"),
  useQueryClient: () => ({ removeQueries: jest.fn() }),
}));
jest.mock(
  "@/components/core/folderSidebarComponent/components/sideBarFolderButtons",
  () => ({
    __esModule: true,
    default: () => <aside>project navigation</aside>,
  }),
);
jest.mock("@/components/ui/sidebar", () => ({
  SidebarProvider: ({ children }) => children,
}));
jest.mock("@/controllers/API/queries/folders", () => ({
  useDeleteFolders: () => ({ mutate: jest.fn() }),
}));
jest.mock("@/customization/components/custom-empty-page", () => ({
  __esModule: true,
  default: () => <p>native welcome</p>,
}));
jest.mock("@/customization/components/custom-loader", () => ({
  __esModule: true,
  default: () => null,
}));
jest.mock("@/customization/hooks/use-custom-navigate", () => ({
  useCustomNavigate: () => jest.fn(),
}));
jest.mock("@/stores/flowsManagerStore", () => ({
  __esModule: true,
  default: (selector) => selector({ flows: [], examples: [] }),
}));
jest.mock("@/stores/foldersStore", () => ({
  useFolderStore: (selector) =>
    selector({ folders: [], setFolderToEdit: jest.fn() }),
}));
jest.mock("../../components/modalsComponent", () => ({
  __esModule: true,
  default: () => null,
}));
afterEach(() => useAuthStore.setState({ platformWorkflowActions: null }));

it("renders the personal files route and navigation before any flow or project exists", () => {
  useAuthStore.setState({ platformWorkflowActions: ["read", "file_read"] });
  render(
    <MemoryRouter initialEntries={["/assets/files"]}>
      <Routes>
        <Route element={<CollectionPage />}>
          <Route path="/assets/files" element={<p>personal files page</p>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
  expect(screen.getByText("personal files page")).toBeInTheDocument();
  expect(screen.getByText("project navigation")).toBeInTheDocument();
  expect(screen.queryByText("native welcome")).not.toBeInTheDocument();
});
