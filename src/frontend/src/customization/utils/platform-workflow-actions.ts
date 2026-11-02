import useAuthStore from "@/stores/authStore";

/** 服务端每次在线判定后返回能力；不存localStorage，不把旧Cookie当编辑授权。 */
export function syncPlatformWorkflowActions(header: unknown) {
  if (typeof header !== "string") return;
  const actions = header
    .split(",")
    .filter((action) =>
      [
        "read",
        "create",
        "update",
        "delete",
        "duplicate",
        "import",
        "export",
        "move",
        "project_create",
        "project_update",
        "project_delete",
        "project_import",
        "project_export",
        "file_read",
        "file_upload",
        "file_download",
        "file_update",
        "file_delete",
        "version_read",
        "version_create",
        "version_restore",
        "version_export",
        "version_delete",
        "dry_run",
        "run",
        "stop",
        "publish",
        "share",
        "api_access",
      ].includes(action),
    );
  const state = useAuthStore.getState();
  if (state.platformWorkflowActions?.join(",") !== actions.join(",")) {
    state.setPlatformWorkflowActions(actions);
  }
}
