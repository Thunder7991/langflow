import useAuthStore from "@/stores/authStore";
import { syncPlatformWorkflowActions } from "../platform-workflow-actions";

afterEach(() => useAuthStore.setState({ platformWorkflowActions: null }));

it("takes the latest server capability and closes editing on an empty failure header", () => {
  syncPlatformWorkflowActions("read,create,update");
  expect(useAuthStore.getState().platformWorkflowActions).toEqual([
    "read",
    "create",
    "update",
  ]);
  syncPlatformWorkflowActions("read");
  expect(useAuthStore.getState().platformWorkflowActions).toEqual(["read"]);
  syncPlatformWorkflowActions("");
  expect(useAuthStore.getState().platformWorkflowActions).toEqual([]);
});

it("preserves native mode for responses without the platform header", () => {
  syncPlatformWorkflowActions(undefined);
  expect(useAuthStore.getState().platformWorkflowActions).toBeNull();
  syncPlatformWorkflowActions("read,delete,execute");
  expect(useAuthStore.getState().platformWorkflowActions).toEqual(["read"]);
});
