import { useTranslations } from "next-intl";
import { setRequestLocale } from "next-intl/server";
import { use } from "react";

type Props = {
  params: Promise<{ locale: string }>;
};

export default function HomePage({ params }: Props) {
  setRequestLocale(use(params).locale);
  const t = useTranslations("app");

  return (
    <>
      <h1>{t("name")}</h1>
      <p>{t("tagline")}</p>
    </>
  );
}
