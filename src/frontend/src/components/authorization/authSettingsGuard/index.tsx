import { CustomNavigate } from "@/customization/components/custom-navigate";
import { ENABLE_PROFILE_ICONS } from "@/customization/feature-flags";
import useAuthStore from "@/stores/authStore";
import { useStoreStore } from "@/stores/storeStore";

export const AuthSettingsGuard = ({ children }) => {
  const autoLogin = useAuthStore((state) => state.autoLogin);
  const platformActions = useAuthStore(
    (state) => state.platformWorkflowActions,
  );
  const hasStore = useStoreStore((state) => state.hasStore);

  // Hides the General settings if there is nothing to show
  const showGeneralSettings =
    platformActions != null || ENABLE_PROFILE_ICONS || hasStore || !autoLogin;

  if (showGeneralSettings) {
    return children;
  } else {
    return <CustomNavigate replace to="/settings/global-variables" />;
  }
};
