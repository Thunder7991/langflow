export type WorkflowPublication = {
  id: string;
  version_number: number;
  source_version_token: string;
  created_at: string;
};

export type WorkflowPublicationState = {
  status: "unpublished" | "published" | "offline";
  publication_token: string | null;
  current: WorkflowPublication | null;
  latest_version: number;
  has_unpublished_changes: boolean;
};
