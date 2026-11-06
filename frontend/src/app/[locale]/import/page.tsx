import { useTranslations } from "next-intl";
import { setRequestLocale } from "next-intl/server";
import { use } from "react";

import { ImportForm } from "@/components/ImportForm";

type Props = {
  params: Promise<{ locale: string }>;
};

export default function ImportPage({ params }: Props) {
  setRequestLocale(use(params).locale);
  const t = useTranslations("import");

  return (
    <>
      <h1>{t("title")}</h1>
      <ImportForm />
    </>
  );
}
