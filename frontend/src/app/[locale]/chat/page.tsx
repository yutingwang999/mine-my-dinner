import { useTranslations } from "next-intl";
import { setRequestLocale } from "next-intl/server";
import { use } from "react";

import { ChatPage } from "@/components/ChatPage";

type Props = {
  params: Promise<{ locale: string }>;
};

export default function ChatRoute({ params }: Props) {
  setRequestLocale(use(params).locale);
  const t = useTranslations("chat");

  return (
    <>
      <h1>{t("title")}</h1>
      <ChatPage />
    </>
  );
}
