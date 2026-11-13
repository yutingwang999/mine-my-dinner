"use client";

import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";

import { DictionarySearch } from "@/components/DictionarySearch";
import { api, ApiError, entryName, type Lang } from "@/lib/api";
import { useErrorText } from "@/lib/errors";

export type ReviewCookware = {
  key: number;
  name: string;
  cookware_id: number | null;
  skipped: boolean;
  linkedName: string | null;
  /** Opens the create form at once, for cookware the user just added by name. */
  startCreating?: boolean;
};

type Props = {
  item: ReviewCookware;
  lang: Lang;
  onChange: (item: ReviewCookware) => void;
};

/**
 * Skipped cookware is not saved, and H3 does not check it. A new entry only records
 * that the dish needs it, so the user is asked whether they own it.
 */
export function CookwareReviewRow({ item, lang, onChange }: Props) {
  const t = useTranslations("line");
  const tEntry = useTranslations("entry");
  const errorText = useErrorText();
  const locale = useLocale();
  const [mode, setMode] = useState<"idle" | "link" | "create">(
    item.startCreating ? "create" : "idle",
  );
  const [nameZh, setNameZh] = useState(lang === "zh" ? item.name : "");
  const [nameEn, setNameEn] = useState(lang === "en" ? item.name : "");
  const [owned, setOwned] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  function link(id: number, name: string) {
    onChange({ ...item, cookware_id: id, linkedName: name, skipped: false });
    setMode("idle");
  }

  async function create() {
    setBusy(true);
    setError(null);
    try {
      const entry = await api.createCookware({
        name_zh: nameZh.trim() || null,
        name_en: nameEn.trim() || null,
        owned,
      });
      link(entry.id, entryName(entry, locale));
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  const ownerId =
    error instanceof ApiError && error.code === "term_conflict" ? Number(error.body.owner_id) : null;

  const state = item.skipped ? "skipped" : item.cookware_id === null ? "unmatched" : "matched";

  return (
    <li
      data-testid={`row-${item.name}`}
      className={`line ${state}`}
    >
      <div className="line-status">
        <strong>{item.name}</strong>
        {item.skipped ? (
          <>
            {t("notNeededDone")}{" "}
            <button type="button" onClick={() => onChange({ ...item, skipped: false })}>
              {t("undoSkip")}
            </button>
          </>
        ) : item.cookware_id !== null ? (
          <>
            {t("matched", { name: item.linkedName ?? item.name })}{" "}
            <button type="button" onClick={() => onChange({ ...item, skipped: true })}>
              {t("notNeeded")}
            </button>
          </>
        ) : (
          <>
            <strong>{t("unmatched")}</strong>
            <button type="button" onClick={() => setMode("link")}>
              {t("linkExisting")}
            </button>
            <button type="button" onClick={() => setMode("create")}>
              {t("createEntry")}
            </button>
            <button type="button" onClick={() => onChange({ ...item, skipped: true })}>
              {t("notNeeded")}
            </button>
          </>
        )}
      </div>
      {mode === "link" && item.cookware_id === null && (
        <DictionarySearch
          search={api.searchCookware}
          onChoose={(entry) => link(entry.id, entryName(entry, locale))}
          onCancel={() => setMode("idle")}
        />
      )}
      {mode === "create" && item.cookware_id === null && (
        <div className="line-panel">
          <label>
            {tEntry("nameZh")}
            <input value={nameZh} onChange={(event) => setNameZh(event.target.value)} />
          </label>
          <label>
            {tEntry("nameEn")}
            <input value={nameEn} onChange={(event) => setNameEn(event.target.value)} />
          </label>
          <label>
            <input
              type="checkbox"
              checked={owned}
              onChange={(event) => setOwned(event.target.checked)}
            />
            {tEntry("owned")}
          </label>
          <button type="button" disabled={busy} onClick={create}>
            {busy ? tEntry("creating") : tEntry("create")}
          </button>
          <button type="button" onClick={() => setMode("idle")}>
            {t("cancel")}
          </button>
          {error !== null && <p role="alert">{errorText(error)}</p>}
          {ownerId !== null && (
            <button type="button" onClick={() => link(ownerId, String((error as ApiError).body.term))}>
              {tEntry("linkToOwner")}
            </button>
          )}
        </div>
      )}
    </li>
  );
}
