import { useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { api } from "@/controllers/API/api";
import { getURL } from "@/controllers/API/helpers/constants";
import { useCustomNavigate } from "@/customization/hooks/use-custom-navigate";
import { createFileUpload } from "@/helpers/create-file-upload";
import { getObjectsFromFilelist } from "@/helpers/get-objects-from-filelist";
import useAuthStore from "@/stores/authStore";
import useFlowStore from "@/stores/flowStore";
import { useFolderStore } from "@/stores/foldersStore";
import type { FlowType } from "@/types/flow";
import { processDataFromFlow } from "@/utils/reactflowUtils";
import useAddFlow from "./use-add-flow";

const useUploadFlow = () => {
  const addFlow = useAddFlow();
  const queryClient = useQueryClient();
  const navigate = useCustomNavigate();
  const { folderId } = useParams();
  const defaultFolder = useFolderStore((state) => state.myCollectionId);
  const paste = useFlowStore((state) => state.paste);

  const getFlowsFromFiles = async ({
    files,
  }: {
    files: File[];
  }): Promise<FlowType[]> => {
    const objectList = await getObjectsFromFilelist<
      FlowType | { flows: FlowType[] }
    >(files);
    const flows: FlowType[] = [];
    objectList.forEach((object) => {
      if ("flows" in object) {
        object.flows.forEach((flow: FlowType) => {
          flows.push(flow);
        });
      } else {
        flows.push(object as FlowType);
      }
    });
    return flows;
  };

  const getFlowsToUpload = async ({
    files,
  }: {
    files?: File[];
  }): Promise<FlowType[]> => {
    if (!files) {
      files = await createFileUpload();
    }
    if (!files.every((file) => file.type === "application/json")) {
      throw new Error("Invalid file type");
    }
    return await getFlowsFromFiles({
      files,
    });
  };

  const uploadFlow = async ({
    files,
    isComponent,
    position,
  }: {
    files?: File[];
    isComponent?: boolean;
    position?: { x: number; y: number };
  }): Promise<void> => {
    const actions = useAuthStore.getState().platformWorkflowActions;
    if (actions != null && !actions.includes("import"))
      throw new Error("当前没有导入工作流权限。");
    try {
      const flows = await getFlowsToUpload({ files });
      if (!flows.length) return;
      if (actions != null && !position) {
        const owner = useAuthStore.getState().userData?.id;
        const response = await api.post<FlowType[]>(
          `${getURL("PLATFORM_WORKFLOW", {}, true)}/import`,
          { flows, folder_id: folderId ?? defaultFolder ?? null },
        );
        if (
          useAuthStore.getState().userData?.id !== owner ||
          !useAuthStore.getState().platformWorkflowActions?.includes("import")
        )
          return;
        void queryClient.invalidateQueries({ queryKey: ["useGetFolder"] });
        void queryClient.invalidateQueries({
          queryKey: ["useGetRefreshFlowsQuery"],
        });
        if (response.data[0]) navigate(`/flow/${response.data[0].id}`);
        return;
      }
      for (const flow of flows) {
        await processDataFromFlow(flow);
      }

      if (
        isComponent !== undefined &&
        flows.every(
          (fileData) =>
            (!fileData.is_component && isComponent === true) ||
            (fileData.is_component !== undefined &&
              fileData.is_component !== isComponent),
        )
      ) {
        throw new Error(
          "You cannot upload a component as a flow or vice versa",
        );
      } else {
        let currentPosition = position;
        for (const flow of flows) {
          if (flow.data) {
            if (currentPosition) {
              paste(flow.data, currentPosition);
              currentPosition = {
                x: currentPosition.x + 50,
                y: currentPosition.y + 50,
              };
            } else {
              await addFlow({ flow });
            }
          } else {
            throw new Error("Invalid flow data");
          }
        }
      }
    } catch (e) {
      throw e;
    }
  };

  return uploadFlow;
};

export default useUploadFlow;
