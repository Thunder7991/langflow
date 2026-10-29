import { useTranslation } from "react-i18next";
import AlertDropdown from "@/alerts/alertDropDown";
import ForwardedIconComponent from "@/components/common/genericIconComponent";
import { SidebarMenuButton } from "@/components/ui/sidebar";
import CustomAccountMenu from "@/customization/components/custom-AccountMenu";
import useAlertStore from "@/stores/alertStore";
import { cn } from "@/utils/utils";

export default function SidebarAccountActions({
  className,
}: {
  className?: string;
}) {
  const { t } = useTranslation();
  const notificationCenter = useAlertStore((state) => state.notificationCenter);

  return (
    <>
      {/* 顶栏隐藏后仍保留原通知列表、未读状态和账号菜单，不另建消息或设置状态。 */}
      <AlertDropdown>
        <SidebarMenuButton
          size="md"
          className={cn("text-sm", className)}
          data-testid="notification_button"
        >
          <span className="relative">
            <ForwardedIconComponent name="Bell" className="h-4 w-4" />
            {notificationCenter && (
              <span className="absolute -right-0.5 -top-0.5 h-1.5 w-1.5 rounded-full bg-destructive" />
            )}
          </span>
          {t("header.notifications")}
        </SidebarMenuButton>
      </AlertDropdown>
      <CustomAccountMenu
        trigger={
          <SidebarMenuButton
            size="md"
            className={cn("text-sm", className)}
            data-testid="user_menu_button"
          >
            <ForwardedIconComponent name="Settings" className="h-4 w-4" />
            {t("account.settings")}
          </SidebarMenuButton>
        }
      />
    </>
  );
}
