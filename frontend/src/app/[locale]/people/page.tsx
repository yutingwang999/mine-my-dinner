import { useTranslations } from "next-intl";
import { setRequestLocale } from "next-intl/server";
import { use } from "react";

import { PeopleManager } from "@/components/PeopleManager";

type Props = {
  params: Promise<{ locale: string }>;
};

export default function PeoplePage({ params }: Props) {
  setRequestLocale(use(params).locale);
  const t = useTranslations("people");

  return (
    <>
      <h1>{t("title")}</h1>
      <PeopleManager />
    </>
  );
}
