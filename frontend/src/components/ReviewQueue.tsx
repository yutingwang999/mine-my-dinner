"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";

import { ConfirmDelete } from "@/components/ConfirmDelete";
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
      <p className="notice notice-info">
        <strong>{t("position", { current: 1, total: drafts.length })}</strong> {t("unsavedNotice")}
      </p>
      <div className="review-layout">
        <section className="card source-panel" aria-label={t("sourceText")}>
          <h2>{t("sourceText")}</h2>
          {drafts[0].source_type === "screenshot" ? (
            <p className="hint">{t("screenshotNoText")}</p>
          ) : (
            <pre className="source-text">{drafts[0].provenance.input_text}</pre>
          )}
        </section>
        <ReviewForm
          key={finished}
          draft={drafts[0]}
          onSaved={next}
          actions={
            <ConfirmDelete
              name={drafts[0].title}
              label={t("discard")}
              question={t("discardQuestion")}
              confirmLabel={t("discardConfirm")}
              onDelete={async () => next()}
            />
          }
        />
      </div>
    </section>
  );
}
