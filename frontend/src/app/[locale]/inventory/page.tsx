import { useTranslations } from "next-intl";
import { setRequestLocale } from "next-intl/server";
import { use } from "react";

import { InventoryManager } from "@/components/InventoryManager";

type Props = {
  params: Promise<{ locale: string }>;
};

export default function InventoryPage({ params }: Props) {
  setRequestLocale(use(params).locale);
  const t = useTranslations("inventory");

  return (
    <>
      <h1>{t("title")}</h1>
      <InventoryManager />
    </>
  );
}
