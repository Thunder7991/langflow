import { Button } from "@/components/ui/button";
import useUploadFlow from "@/hooks/flows/use-upload-flow";
import useAlertStore from "@/stores/alertStore";
import { extractApiErrorMessages } from "@/utils/apiError";

export default function PlatformImportButton() {
  const upload = useUploadFlow();
  return (
    <Button
      size="sm"
      variant="outline"
      onClick={() => {
        void upload({}).catch((error) =>
          useAlertStore.getState().setErrorData({
            title: "导入工作流失败",
            list: extractApiErrorMessages(error),
          }),
        );
      }}
    >
      导入工作流
    </Button>
  );
}
