"use client";

import { useTranslations } from "next-intl";
import { useEffect, useRef, useState } from "react";

import { ConfirmDelete } from "@/components/ConfirmDelete";
import { ReviewForm } from "@/components/ReviewForm";
import { ScreenshotPreview } from "@/components/ScreenshotPreview";
import { Link } from "@/i18n/navigation";
import { useDrafts } from "@/lib/drafts";

export function ReviewQueue() {
  const t = useTranslations("review");
  const { drafts, removeFirst } = useDrafts();
  // Each finished draft gets a fresh form, so no state carries over.
  const [finished, setFinished] = useState(0);
  const sourcePanel = useRef<HTMLElement>(null);

  // The save button sits at the bottom of a long form, so without this the
  // next draft would appear scrolled to the same depth.
  useEffect(() => {
    if (finished === 0) return;
    window.scrollTo(0, 0);
    if (sourcePanel.current) {
      sourcePanel.current.scrollTop = 0;
    }
  }, [finished]);

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
        <section ref={sourcePanel} className="card source-panel" aria-label={t("sourceText")}>
          <h2>{t("sourceText")}</h2>
          {drafts[0].source_type === "screenshot" ? (
            <>
              <p className="hint">{t("screenshotsKept")}</p>
              <ScreenshotPreview files={drafts[0].images ?? []} />
            </>
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
