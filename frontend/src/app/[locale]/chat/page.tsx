import { redirect } from "@/i18n/navigation";

type Props = {
  params: Promise<{ locale: string }>;
};

/** The chat lives at the home page; this keeps old links working. */
export default async function ChatRoute({ params }: Props) {
  const { locale } = await params;
  redirect({ href: "/", locale });
}
