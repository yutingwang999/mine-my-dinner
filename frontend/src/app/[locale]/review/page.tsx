import { useTranslations } from "next-intl";
import { setRequestLocale } from "next-intl/server";
import { use } from "react";

import { ReviewQueue } from "@/components/ReviewQueue";

type Props = {
  params: Promise<{ locale: string }>;
};

export default function ReviewPage({ params }: Props) {
  setRequestLocale(use(params).locale);
  const t = useTranslations("review");

  return (
    <>
      <h1>{t("title")}</h1>
      <ReviewQueue />
    </>
  );
}
