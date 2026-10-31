import { useCallback } from "react";
import { useTranslation } from "react-i18next";
import {
  PLATFORM_READ_ONLY_REASON,
  PLATFORM_UNAVAILABLE_REASON,
} from "@/customization/utils/platform-workflow-ui";
import useAuthStore from "@/stores/authStore";

const useSelectOptionsChange = (
  selectedFlowsComponentsCards: string[] | undefined,
  setErrorData: (data: { title: string; list: string[] }) => void,
  setOpenDelete: (value: boolean) => void,
  handleExport: () => void,
  handleDuplicate: () => void,
  handleEdit: () => void,
) => {
  const { t } = useTranslation();
  const handleSelectOptionsChange = useCallback(
    (action) => {
      const actions = useAuthStore.getState().platformWorkflowActions;
      if (
        actions != null &&
        (action !== "edit" || !actions.includes("update"))
      ) {
        setErrorData({
          title: t("errors.saveChanges"),
          list: [
            action === "edit"
              ? PLATFORM_READ_ONLY_REASON
              : PLATFORM_UNAVAILABLE_REASON,
          ],
        });
        return;
      }
      const hasSelected = selectedFlowsComponentsCards?.length! > 0;
      if (!hasSelected) {
        setErrorData({
          title: t("errors.noItemsSelected"),
          list: [t("errors.selectItemsToDelete")],
        });
        return;
      }
      if (action === "delete") {
        setOpenDelete(true);
      } else if (action === "duplicate") {
        handleDuplicate();
      } else if (action === "export") {
        handleExport();
      } else if (action === "edit") {
        handleEdit();
      }
    },
    [
      selectedFlowsComponentsCards,
      setErrorData,
      setOpenDelete,
      handleDuplicate,
      handleEdit,
      handleExport,
    ],
  );

  return { handleSelectOptionsChange };
};

export default useSelectOptionsChange;
