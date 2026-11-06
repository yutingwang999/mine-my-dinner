import type { Metadata } from "next";
import { hasLocale, NextIntlClientProvider } from "next-intl";
import { setRequestLocale } from "next-intl/server";
import { notFound } from "next/navigation";
import type { ReactNode } from "react";

import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { MainNav } from "@/components/MainNav";
import { routing } from "@/i18n/routing";
import { DraftsProvider } from "@/lib/drafts";

import "../globals.css";

export const metadata: Metadata = { title: "MineMyDinner" };

export function generateStaticParams() {
  return routing.locales.map((locale) => ({ locale }));
}

type Props = {
  children: ReactNode;
  params: Promise<{ locale: string }>;
};

export default async function LocaleLayout({ children, params }: Props) {
  const { locale } = await params;
  if (!hasLocale(routing.locales, locale)) {
    notFound();
  }
  setRequestLocale(locale);

  return (
    <html lang={locale}>
      <body>
        <NextIntlClientProvider>
          {/* Drafts live here, so moving between import and review keeps them. */}
          <DraftsProvider>
            <header style={{ padding: "1rem" }}>
              <MainNav />
              <LanguageSwitcher />
            </header>
            <main style={{ padding: "1rem" }}>{children}</main>
          </DraftsProvider>
        </NextIntlClientProvider>
      </body>
    </html>
  );
}
