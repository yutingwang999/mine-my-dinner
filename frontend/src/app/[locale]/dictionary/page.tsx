import { useTranslations } from "next-intl";
import { setRequestLocale } from "next-intl/server";
import { use } from "react";

import { DictionaryManager } from "@/components/DictionaryManager";

type Props = {
  params: Promise<{ locale: string }>;
};

export default function DictionaryPage({ params }: Props) {
  setRequestLocale(use(params).locale);
  const t = useTranslations("dictionary");

  return (
    <>
      <h1>{t("title")}</h1>
      <DictionaryManager />
    </>
  );
}
