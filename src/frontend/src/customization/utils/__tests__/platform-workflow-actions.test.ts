import useAuthStore from "@/stores/authStore";
import { syncPlatformWorkflowActions } from "../platform-workflow-actions";

it("accepts explicit operation actions while rejecting unknown execute alias", () => {
  syncPlatformWorkflowActions("read,dry_run,execute,publish");
  expect(useAuthStore.getState().platformWorkflowActions).toEqual([
    "read",
    "dry_run",
    "publish",
  ]);
});

it("keeps explicit file capabilities without deriving them from flow update", () => {
  syncPlatformWorkflowActions(
    "read,update,file_read,file_upload,file_download,delete",
  );
  expect(useAuthStore.getState().platformWorkflowActions).toEqual([
    "read",
    "update",
    "file_read",
    "file_upload",
    "file_download",
    "delete",
  ]);
});

afterEach(() => useAuthStore.setState({ platformWorkflowActions: null }));

it("keeps explicit project, history and execution grants without deriving editing", () => {
  syncPlatformWorkflowActions(
    "read,project_create,version_read,run,stop,publish,project_delete",
  );
  expect(useAuthStore.getState().platformWorkflowActions).toEqual([
    "read",
    "project_create",
    "version_read",
    "run",
    "stop",
    "publish",
    "project_delete",
  ]);
});

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
  expect(useAuthStore.getState().platformWorkflowActions).toEqual([
    "read",
    "delete",
  ]);
});
