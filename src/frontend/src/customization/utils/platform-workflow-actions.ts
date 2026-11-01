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
        "dry_run",
        "file_read",
        "file_upload",
        "file_download",
      ].includes(action),
    );
  const state = useAuthStore.getState();
  if (state.platformWorkflowActions?.join(",") !== actions.join(",")) {
    state.setPlatformWorkflowActions(actions);
  }
}
