"use client";

import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";

import { AllergenPicker } from "@/components/AllergenPicker";
import { DictionarySearch } from "@/components/DictionarySearch";
import { EntryTypeFields, useEntryType } from "@/components/EntryTypeFields";
import { type Allergen, api, entryName, type IngredientEntry } from "@/lib/api";
import { useErrorText } from "@/lib/errors";

/** A name with no dictionary entry yet, or an entry whose allergens are unknown. */
export type Target = { name: string } | { ingredient_id: number };

/** Shown when a link was asked to be remembered but the name belongs to another entry. */
export type LinkNotice = { name: string; entry: string };

/** What linking a name returned: the entry, and how many unlinked lines it now covers. */
export type LinkResult = IngredientEntry & { linked_lines: number; remembered: boolean };

/** Saving needs a confirmed value: a list, or an explicit "contains none". */
export function ConfirmPanel({
  name,
  target,
  onCancel,
  onResolved,
  switchTo,
}: {
  name: string;
  target: Target;
  onCancel: () => void;
  onResolved: () => void;
  /** A link at the left of the action row to the other way of resolving the name. */
  switchTo?: ReactNode;
}) {
  const t = useTranslations("dictionary");
  const tCommon = useTranslations("common");
  const errorText = useErrorText();
  const [allergens, setAllergens] = useState<Allergen[] | null>(null);
  const type = useEntryType("other");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  // Only a name that has no entry yet becomes a new entry, so only it has a category.
  const isNewEntry = "name" in target;

  async function save() {
    if (allergens === null) return;
    setBusy(true);
    setError(null);
    try {
      await api.resolveAllergens(
        target,
        allergens,
        isNewEntry ? { category: type.category, stock_mode: type.stockMode } : undefined,
      );
      onResolved();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="dish-panel form-panel">
      {isNewEntry && <EntryTypeFields type={type} fieldClass="panel-row" />}
      <AllergenPicker name={name} onChange={setAllergens} onSuggestion={type.applySuggestion} />
      {error !== null && (
        <p role="alert" className="notice notice-warning">
          {errorText(error)}
        </p>
      )}
      <div className="panel-actions">
        {switchTo && <span className="panel-switch">{switchTo}</span>}
        <button type="button" disabled={busy} onClick={onCancel}>
          {tCommon("cancel")}
        </button>
        <button type="button" className="btn-primary" disabled={allergens === null || busy} onClick={save}>
          {t("saveAllergens")}
        </button>
      </div>
    </div>
  );
}

/** Links every line with an unmatched name to an entry the user picks. */
export function LinkPanel({
  name,
  onCancel,
  onResolved,
  switchTo,
}: {
  name: string;
  onCancel: () => void;
  /** A link at the left of the action row to the other way of resolving the name. */
  switchTo?: ReactNode;
  /**
   * Gets the link notice to show when the name could not be remembered, else null, and the
   * link result (the entry, with how many lines were linked).
   */
  onResolved: (notice: LinkNotice | null, linked: LinkResult) => void;
}) {
  const t = useTranslations("dictionary");
  const tReview = useTranslations("review");
  const tCommon = useTranslations("common");
  const locale = useLocale();
  const errorText = useErrorText();
  const [chosen, setChosen] = useState<IngredientEntry | null>(null);
  const [remember, setRemember] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function confirm() {
    if (chosen === null) return;
    setBusy(true);
    setError(null);
    try {
      const linked = await api.linkUnknownName(name, chosen.id, remember);
      // Asked to remember, but the name already belongs to another entry.
      onResolved(remember && !linked.remembered ? { name, entry: entryName(chosen, locale) } : null, linked);
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="dish-panel form-panel">
      <DictionarySearch search={api.searchIngredients} onChoose={setChosen} selectedId={chosen?.id ?? null} />
      {chosen !== null && <p role="status">{t("linkChosen", { entry: entryName(chosen, locale) })}</p>}
      <label className="mini-field inline">
        <input type="checkbox" checked={remember} onChange={(event) => setRemember(event.target.checked)} />
        {tReview("rememberLink")}
      </label>
      {error !== null && (
        <p role="alert" className="notice notice-warning">
          {errorText(error)}
        </p>
      )}
      <div className="panel-actions">
        {switchTo && <span className="panel-switch">{switchTo}</span>}
        <button type="button" disabled={busy} onClick={onCancel}>
          {tCommon("cancel")}
        </button>
        <button type="button" className="btn-primary" disabled={chosen === null || busy} onClick={confirm}>
          {t("confirmLink")}
        </button>
      </div>
    </div>
  );
}

type ResolveState =
  | { kind: "idle" }
  | { kind: "checking" }
  /** A name with no dictionary entry: link it, or create an entry with its allergens. */
  | { kind: "link" }
  | { kind: "create" }
  /** An entry whose allergens are unknown. */
  | { kind: "entry"; id: number }
  /**
   * The name matches an entry whose allergens are known, so the dish's line is not linked
   * to it (an unknown-allergen line is unlinked or on an entry with unknown allergens).
   */
  | { kind: "known" }
  /** A link found no unlinked line with this name: nothing changed for the dish. */
  | { kind: "unchanged" }
  | { kind: "done" };

/**
 * The way to confirm an unknown-allergen ingredient name of a dish (plan 2b): a button, then
 * the panel the dictionary calls for. Renders the button group and the panel as siblings, so
 * the caller lays them out in its own row.
 */
export function ResolveUnknownName({ name, onResolved }: { name: string; onResolved: () => void }) {
  const t = useTranslations("chat.plan");
  const tDictionary = useTranslations("dictionary");
  const errorText = useErrorText();
  const [state, setState] = useState<ResolveState>({ kind: "idle" });
  const [error, setError] = useState<unknown>(null);
  // Shown when a link was asked to be remembered but the name belongs to another entry.
  const [linkNotice, setLinkNotice] = useState<LinkNotice | null>(null);
  const idle = () => setState({ kind: "idle" });
  const done = () => {
    setState({ kind: "done" });
    onResolved();
  };
  // A link fixes the dish only when it linked a line to an entry whose allergens are known.
  const linked = (notice: LinkNotice | null, result: LinkResult) => {
    setLinkNotice(notice);
    if (result.linked_lines === 0) setState({ kind: "unchanged" });
    else if (result.allergens === null) setState({ kind: "entry", id: result.id });
    else done();
  };

  async function check() {
    setState({ kind: "checking" });
    setError(null);
    try {
      // The dictionary decides the panel: no entry, an entry with unknown allergens, or known ones.
      const { matches } = await api.matchIngredients([name]);
      const entry = matches.find((match) => match.name === name)?.entry;
      if (entry === undefined) {
        setState({ kind: "link" });
      } else if (entry.allergens === null) {
        setState({ kind: "entry", id: entry.id });
      } else {
        setState({ kind: "known" });
      }
    } catch (caught) {
      setError(caught);
      idle();
    }
  }

  return (
    <>
      {state.kind === "idle" && (
        <span className="actions">
          <button type="button" onClick={check}>
            {t("leftOutConfirm")}
          </button>
        </span>
      )}
      <div className="left-out-panel">
        {state.kind === "checking" && <p className="hint">{t("leftOutChecking")}</p>}
        {error !== null && (
          <p role="alert" className="notice notice-warning">
            {errorText(error)}
          </p>
        )}
        {state.kind === "link" && (
          <LinkPanel
            name={name}
            onCancel={idle}
            onResolved={linked}
            switchTo={
              <button type="button" className="btn-text" onClick={() => setState({ kind: "create" })}>
                {t("leftOutCreateInstead")}
              </button>
            }
          />
        )}
        {state.kind === "create" && (
          <ConfirmPanel
            name={name}
            target={{ name }}
            onCancel={idle}
            onResolved={done}
            switchTo={
              <button type="button" className="btn-text" onClick={() => setState({ kind: "link" })}>
                {t("leftOutLinkInstead")}
              </button>
            }
          />
        )}
        {state.kind === "entry" && (
          <ConfirmPanel name={name} target={{ ingredient_id: state.id }} onCancel={idle} onResolved={done} />
        )}
        {state.kind === "known" && (
          <>
            <p role="status">{t("leftOutKnown")}</p>
            <LinkPanel name={name} onCancel={idle} onResolved={linked} />
          </>
        )}
        {state.kind === "unchanged" && <p role="status">{t("leftOutNothingLinked")}</p>}
        {state.kind === "done" && <p role="status">{t("leftOutConfirmed")}</p>}
        {linkNotice !== null && (state.kind === "done" || state.kind === "entry") && (
          <p className="notice notice-info">{tDictionary("linkNotRemembered", linkNotice)}</p>
        )}
      </div>
    </>
  );
}
