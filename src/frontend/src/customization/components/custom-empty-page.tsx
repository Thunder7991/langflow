import { useStartNewFlow } from "@/components/core/flowBuilderWelcome/hooks/use-start-new-flow";
import { EmptyPageCommunity } from "@/pages/MainPage/pages/empty-page";
import EmptyFolder from "@/pages/MainPage/pages/emptyFolder";
import useAuthStore from "@/stores/authStore";

export const CustomEmptyPageCommunity = ({
  setOpenModal,
}: {
  setOpenModal: (open: boolean) => void;
}) => {
  const isPlatform = useAuthStore(
    (state) => state.platformWorkflowActions != null,
  );
  if (isPlatform) return <PlatformEmptyPage setOpenModal={setOpenModal} />;
  return <EmptyPageCommunity setOpenModal={setOpenModal} />;
};
function PlatformEmptyPage({
  setOpenModal,
}: {
  setOpenModal: (open: boolean) => void;
}) {
  const startNewFlow = useStartNewFlow();
  return (
    <EmptyFolder
      setOpenModal={setOpenModal}
      onNewFlow={() => {
        void startNewFlow().catch(() => {});
      }}
    />
  );
}
export default CustomEmptyPageCommunity;
