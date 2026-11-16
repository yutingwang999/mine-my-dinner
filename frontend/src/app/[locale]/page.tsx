import { setRequestLocale } from "next-intl/server";
import { use } from "react";

import { ChatPage } from "@/components/ChatPage";

type Props = {
  params: Promise<{ locale: string }>;
};

/** The home page is the chat. */
export default function HomePage({ params }: Props) {
  setRequestLocale(use(params).locale);
  return <ChatPage />;
}
