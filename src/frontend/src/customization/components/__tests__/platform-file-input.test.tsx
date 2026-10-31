import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { usePlatformFiles } from "@/controllers/API/queries/platform-files/personal-files";
import useAuthStore from "@/stores/authStore";
import PlatformFileInput from "../platform-file-input";

jest.mock("@/controllers/API/queries/platform-files/personal-files", () => ({
  usePlatformFiles: jest.fn(),
}));
jest.mock("@/customization/components/custom-link", () => ({
  CustomLink: ({ children }) => <span>{children}</span>,
}));
beforeEach(() => {
  useAuthStore.setState({
    platformWorkflowActions: ["read", "update", "file_read"],
  });
  (usePlatformFiles as jest.Mock).mockReturnValue({
    data: [{ id: "one", name: "a.txt", path: "owner/one.txt" }],
    isPending: false,
    refetch: jest.fn(),
  });
});
afterEach(() => useAuthStore.setState({ platformWorkflowActions: null }));

it("selects real file names and paths together and never clears existing refs on failure", () => {
  const change = jest.fn();
  const view = render(
    <PlatformFileInput
      paths={[]}
      value={[]}
      disabled={false}
      multiple={false}
      onChange={change}
    />,
  );
  fireEvent.change(screen.getByRole("listbox"), {
    target: { value: "owner/one.txt" },
  });
  expect(change).toHaveBeenCalledWith({
    value: ["a.txt"],
    file_path: ["owner/one.txt"],
  });
  (usePlatformFiles as jest.Mock).mockReturnValue({
    data: undefined,
    error: new Error("offline"),
    refetch: jest.fn(),
  });
  view.rerender(
    <PlatformFileInput
      paths={["owner/one.txt"]}
      value={["a.txt"]}
      disabled={false}
      multiple={false}
      onChange={change}
    />,
  );
  expect(screen.getByText("a.txt")).toBeInTheDocument();
  expect(change).toHaveBeenCalledTimes(1);
});

it("blocks file changes after update revocation while keeping current references", async () => {
  const change = jest.fn();
  render(
    <PlatformFileInput
      paths={["owner/one.txt"]}
      value={["a.txt"]}
      disabled={false}
      multiple={false}
      onChange={change}
    />,
  );
  useAuthStore.setState({ platformWorkflowActions: ["read", "file_read"] });
  await waitFor(() => expect(screen.getByRole("listbox")).toBeDisabled());
  fireEvent.change(screen.getByRole("listbox"), {
    target: { value: "owner/one.txt" },
  });
  expect(change).not.toHaveBeenCalled();
});
