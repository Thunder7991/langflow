import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import IconComponent from "@/components/common/genericIconComponent";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { useUpdateUser } from "@/controllers/API/queries/auth";
import CustomGetStartedProgress from "@/customization/components/custom-get-started-progress";
import useAuthStore from "@/stores/authStore";
import { useUtilityStore } from "@/stores/utilityStore";
import { cn } from "@/utils/utils";
import { AddFolderButton } from "./add-folder-button";
import { UploadFolderButton } from "./upload-folder-button";

// 暂时停用熟悉进度区域，连同分隔线一起不挂载；恢复时开启此开关即可。
const SHOW_GETTING_STARTED_PROGRESS = false;

export const HeaderButtons = ({
  handleUploadFlowsToFolder,
  isUpdatingFolder,
  isPending,
  addNewFolder,
  containerClassName,
  titleClassName,
}: {
  handleUploadFlowsToFolder: () => void;
  isUpdatingFolder: boolean;
  isPending: boolean;
  addNewFolder: () => void;
  containerClassName?: string;
  titleClassName?: string;
}) => {
  const { t } = useTranslation();
  const userData = useAuthStore((state) => state.userData);
  const platformActions = useAuthStore(
    (state) => state.platformWorkflowActions,
  );
  const canCreateProject =
    platformActions == null || platformActions.includes("project_create");
  const hideGettingStartedProgress = useUtilityStore(
    (state) => state.hideGettingStartedProgress,
  );
  const hideNewProjectButton = useUtilityStore(
    (state) => state.hideNewProjectButton,
  );

  const [isDismissedDialog, setIsDismissedDialog] = useState(
    userData?.optins?.dialog_dismissed,
  );
  const [isGithubStarred, setIsGithubStarred] = useState(
    userData?.optins?.github_starred,
  );
  const [isDiscordJoined, setIsDiscordJoined] = useState(
    userData?.optins?.discord_clicked,
  );

  const { mutate: updateUser } = useUpdateUser();

  useEffect(() => {
    if (userData) {
      setIsDismissedDialog(userData.optins?.dialog_dismissed);
      setIsGithubStarred(userData.optins?.github_starred);
      setIsDiscordJoined(userData.optins?.discord_clicked);
    }
  }, [userData]);

  const handleDismissDialog = () => {
    setIsDismissedDialog(true);
    updateUser({
      user_id: userData?.id!,
      user: {
        optins: {
          ...userData?.optins,
          dialog_dismissed: true,
        },
      },
    });
  };

  return (
    <>
      {SHOW_GETTING_STARTED_PROGRESS &&
        !hideGettingStartedProgress &&
        !isDismissedDialog &&
        userData && (
          <CustomGetStartedProgress
            userData={userData}
            isGithubStarred={isGithubStarred ?? false}
            isDiscordJoined={isDiscordJoined ?? false}
            handleDismissDialog={handleDismissDialog}
          />
        )}

      <div
        className={cn(
          "flex shrink-0 items-center justify-between gap-2 pt-2",
          containerClassName,
        )}
      >
        <SidebarTrigger className="lg:hidden">
          <IconComponent name="PanelLeftClose" className="h-4 w-4" />
        </SidebarTrigger>

        <div
          id="project-sidebar-title"
          className={cn("flex-1 text-sm font-medium", titleClassName)}
        >
          {t("sidebar.projects")}
        </div>
        <div className="flex items-center gap-1">
          {platformActions == null && (
            <UploadFolderButton
              onClick={handleUploadFlowsToFolder}
              disabled={isUpdatingFolder}
            />
          )}
          {!hideNewProjectButton && canCreateProject && (
            <AddFolderButton
              onClick={addNewFolder}
              disabled={isUpdatingFolder || !canCreateProject}
              loading={isPending}
            />
          )}
        </div>
      </div>
    </>
  );
};
