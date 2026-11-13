"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";

import { useErrorText } from "@/lib/errors";

type Props = {
  name: string;
  /** What else the delete changes, shown before the user confirms. */
  notice?: string;
  onDelete: () => Promise<unknown>;
  /** Wording for actions other than deleting, such as discarding a draft. */
  label?: string;
  question?: string;
  confirmLabel?: string;
};

/** A delete button that asks inline first, without a browser dialog. */
export function ConfirmDelete({
  name,
  notice,
  onDelete,
  label,
  question,
  confirmLabel,
}: Props) {
  const t = useTranslations("common");
  const errorText = useErrorText();
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function confirm() {
    setBusy(true);
    setError(null);
    try {
      await onDelete();
      setAsking(false);
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  if (!asking) {
    return (
      <button type="button" onClick={() => setAsking(true)}>
        {label ?? t("delete")}
      </button>
    );
  }
  return (
    <span role="group" className="confirm-box">
      <span>{question ?? t("confirmDelete", { name })}</span> {notice && <span>{notice}</span>}{" "}
      <button type="button" className="danger" disabled={busy} onClick={confirm}>
        {confirmLabel ?? t("yesDelete")}
      </button>
      <button type="button" onClick={() => setAsking(false)}>
        {t("cancel")}
      </button>
      {error !== null && <span role="alert">{errorText(error)}</span>}
    </span>
  );
}
