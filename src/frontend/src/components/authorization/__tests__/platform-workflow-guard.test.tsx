import { render, screen } from "@testing-library/react";
import { useEffect } from "react";
import { MemoryRouter } from "react-router-dom";
import useAuthStore from "@/stores/authStore";
import { PlatformWorkflowGuard } from "../platformWorkflowGuard";

afterEach(() => useAuthStore.setState({ platformWorkflowActions: null }));

it("allows My Files with explicit file_read while keeping other asset pages closed", () => {
  useAuthStore.setState({ platformWorkflowActions: ["read", "file_read"] });
  render(
    <MemoryRouter initialEntries={["/assets/files"]}>
      <PlatformWorkflowGuard>
        <div>personal files</div>
      </PlatformWorkflowGuard>
    </MemoryRouter>,
  );
  expect(screen.getByText("personal files")).toBeInTheDocument();
});

it.each([
  "/assets/files",
  "/assets/knowledge-bases",
  "/mcp/folder/project",
  "/components",
  "/settings/mcp-servers",
  "/settings/api-keys",
  "/settings/model-providers",
  "/settings/messages",
  "/settings/connections",
  "/settings/global-variables",
])("blocks %s before its page can send requests", (path) => {
  useAuthStore.setState({
    platformWorkflowActions: ["read", "create", "update"],
  });
  const mounted = jest.fn();
  function ManagementPage() {
    useEffect(mounted, []);
    return <div>management form</div>;
  }
  render(
    <MemoryRouter initialEntries={[path]}>
      <PlatformWorkflowGuard>
        <ManagementPage />
      </PlatformWorkflowGuard>
    </MemoryRouter>,
  );
  expect(mounted).not.toHaveBeenCalled();
  expect(screen.queryByText("management form")).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: "返回工作流列表" })).toHaveAttribute(
    "href",
    "/flows",
  );
});

it.each([
  "/flows",
  "/all/folder/project",
  "/flow/own-flow",
  "/settings/general",
  "/settings/shortcuts",
])("keeps the platform editor and local preference route %s", (path) => {
  useAuthStore.setState({ platformWorkflowActions: ["read"] });
  render(
    <MemoryRouter initialEntries={[path]}>
      <PlatformWorkflowGuard>
        <div>allowed page</div>
      </PlatformWorkflowGuard>
    </MemoryRouter>,
  );
  expect(screen.getByText("allowed page")).toBeInTheDocument();
});

it("leaves native management pages available", () => {
  useAuthStore.setState({ platformWorkflowActions: null });
  render(
    <MemoryRouter initialEntries={["/settings/mcp-servers"]}>
      <PlatformWorkflowGuard>
        <div>native page</div>
      </PlatformWorkflowGuard>
    </MemoryRouter>,
  );
  expect(screen.getByText("native page")).toBeInTheDocument();
});
