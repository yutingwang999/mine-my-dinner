import { useTranslations } from "next-intl";
import { setRequestLocale } from "next-intl/server";
import { use } from "react";

import { RecipeLibrary } from "@/components/RecipeLibrary";

type Props = {
  params: Promise<{ locale: string }>;
};

export default function LibraryPage({ params }: Props) {
  setRequestLocale(use(params).locale);
  const t = useTranslations("library");

  return (
    <>
      <h1>{t("title")}</h1>
      <RecipeLibrary />
    </>
  );
}
