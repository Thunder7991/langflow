import type { ComponentProps } from "react";
import { AccountMenu } from "@/components/core/appHeaderComponent/components/AccountMenu";

export function CustomAccountMenu(props: ComponentProps<typeof AccountMenu>) {
  return <AccountMenu {...props} />;
}

export default CustomAccountMenu;
