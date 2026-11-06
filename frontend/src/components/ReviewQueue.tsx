"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";

import { ReviewForm } from "@/components/ReviewForm";
import { Link } from "@/i18n/navigation";
import { useDrafts } from "@/lib/drafts";

export function ReviewQueue() {
  const t = useTranslations("review");
  const { drafts, removeFirst } = useDrafts();
  // Each finished draft gets a fresh form, so no state carries over.
  const [finished, setFinished] = useState(0);

  if (drafts.length === 0) {
    return (
      <p>
        {t("empty")} <Link href="/import">{t("goImport")}</Link>
      </p>
    );
  }

  function next() {
    removeFirst();
    setFinished((count) => count + 1);
  }

  return (
    <section>
      <p>{t("unsavedNotice")}</p>
      <p>{t("position", { current: 1, total: drafts.length })}</p>
      <ReviewForm key={finished} draft={drafts[0]} onSaved={next} />
      <button type="button" onClick={next}>
        {t("discard")}
      </button>
    </section>
  );
}
