"use client";

import { useLocale, useTranslations } from "next-intl";
import { useEffect, useState } from "react";

import { AllergenPicker } from "@/components/AllergenPicker";
import { DictionarySearch, namesEntry } from "@/components/DictionarySearch";
import { EntryTypeFields, useEntryType } from "@/components/EntryTypeFields";
import {
  type Allergen,
  api,
  ApiError,
  entryName,
  type IngredientLine,
  type Lang,
  ROLES,
  type Role,
  type Unit,
  UNITS,
} from "@/lib/api";
import { amountProblem, typeForUnit } from "@/lib/amounts";
import { Link } from "@/i18n/navigation";
import { useErrorText } from "@/lib/errors";

export type ReviewLine = IngredientLine & {
  key: number;
  skipped: boolean;
  linkedName: string | null;
  /** Added on the review page, so it has no source wording of its own. */
  added: boolean;
  /** The line's own name matched an entry before the user did anything with it. */
  startedMatched: boolean;
  /** The user linked this line by hand, so the form offers to remember the link. */
  linkedByHand: boolean;
  /** Save the name as an alias of the linked entry; only meaningful when linkedByHand. */
  rememberLink: boolean;
  /** The user linked, unlinked, skipped, or renamed the line; a re-match leaves it alone. */
  touched: boolean;
};

type Props = {
  line: ReviewLine;
  lang: Lang;
  /** The drag handle's accessible name, such as "拖动酸奶". */
  dragLabel: string;
  /** True while any line is being dragged; only then do rows intercept drops. */
  dragging: boolean;
  /** Starts dragging this line; dropping it on another line makes them a choice. */
  onDragStart: () => void;
  /** The drag ended anywhere, even cancelled; the form clears its drag state. */
  onDragEnd: () => void;
  /** A dragged line was dropped on this one. */
  onDropLine: () => void;
  /** True inside a choice card: the card carries the group's one optional checkbox. */
  grouped: boolean;
  /** Takes the line out of its choice group; null when the line is not in one. */
  onUngroup: (() => void) | null;
  onChange: (line: ReviewLine) => void;
  onRemove: () => void;
};

type Mode = "idle" | "link" | "create";

function CreateIngredient({
  line,
  lang,
  onCreated,
  onCancel,
}: {
  line: ReviewLine;
  lang: Lang;
  onCreated: (id: number, name: string) => void;
  onCancel: () => void;
}) {
  const t = useTranslations("entry");
  const tLine = useTranslations("line");
  const errorText = useErrorText();
  const [nameZh, setNameZh] = useState(lang === "zh" ? line.name : "");
  const [nameEn, setNameEn] = useState(lang === "en" ? line.name : "");
  const type = useEntryType(line.role === "seasoning" ? "seasoning" : "other");
  const [allergens, setAllergens] = useState<Allergen[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const locale = useLocale();
  // The entry that already owns one of the typed names, found before the user submits.
  const [found, setOwner] = useState<{ key: string; term: string; id: number; entry: string } | null>(null);

  const namesKey = [nameZh.trim(), nameEn.trim()].filter(Boolean).join("|");

  useEffect(() => {
    if (!namesKey) return;
    const names = namesKey.split("|");
    let active = true;
    const timer = setTimeout(() => {
      api
        .matchIngredients(names)
        .then(({ matches }) => {
          if (!active) return;
          const match = matches[0];
          setOwner(
            match
              ? { key: namesKey, term: match.name, id: match.entry.id, entry: entryName(match.entry, locale) }
              : null,
          );
        })
        // A failed check shows nothing; the server still refuses a taken name.
        .catch(() => active && setOwner(null));
    }, 300);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [namesKey, locale]);

  // An answer for names the user has since changed no longer counts.
  const owner = found !== null && found.key === namesKey ? found : null;

  async function create() {
    setBusy(true);
    setError(null);
    try {
      const entry = await api.createIngredient({
        name_zh: nameZh.trim() || null,
        name_en: nameEn.trim() || null,
        category: type.category,
        stock_mode: type.stockMode,
        allergens,
      });
      onCreated(entry.id, nameZh.trim() || nameEn.trim());
    } catch (caught) {
      setError(caught instanceof ApiError ? caught : new ApiError(0, "unknown"));
    } finally {
      setBusy(false);
    }
  }

  const ownerId = error?.code === "term_conflict" ? Number(error.body.owner_id) : null;

  return (
    <div>
      {owner && (
        <div className="notice notice-warning actions">
          <span>{t("ownedName", { term: owner.term, entry: owner.entry })}</span>
          <button type="button" onClick={() => onCreated(owner.id, owner.entry)}>
            {t("linkToEntry", { entry: owner.entry })}
          </button>
          <Link href="/dictionary" target="_blank">
            {t("manageInDictionary")}
          </Link>
        </div>
      )}
      <label className="mini-field">
        {t("nameZh")}
        <input value={nameZh} onChange={(event) => setNameZh(event.target.value)} />
      </label>
      <label className="mini-field">
        {t("nameEn")}
        <input value={nameEn} onChange={(event) => setNameEn(event.target.value)} />
      </label>
      <EntryTypeFields type={type} fieldClass="mini-field" />
      <AllergenPicker name={line.name} onChange={setAllergens} onSuggestion={type.applySuggestion} />
      <button type="button" className="btn-primary" disabled={busy || owner !== null} onClick={create}>
        {busy ? t("creating") : t("create")}
      </button>
      <button type="button" onClick={onCancel}>
        {tLine("cancel")}
      </button>
      {error && <p role="alert">{errorText(error)}</p>}
      {ownerId !== null && (
        <button type="button" onClick={() => onCreated(ownerId, String(error?.body.term ?? ""))}>
          {t("linkToOwner")}
        </button>
      )}
    </div>
  );
}

export function IngredientReviewRow({
  line,
  lang,
  dragLabel,
  dragging,
  onDragStart,
  onDragEnd,
  onDropLine,
  grouped,
  onUngroup,
  onChange,
  onRemove,
}: Props) {
  const t = useTranslations("line");
  const tUnit = useTranslations("unit");
  const tAmount = useTranslations("amountType");
  const tRole = useTranslations("role");
  const tReview = useTranslations("review");
  const locale = useLocale();
  const [mode, setMode] = useState<Mode>("idle");
  // The link before "Change link", so the change can be undone.
  const [previous, setPrevious] = useState<{ id: number; name: string } | null>(null);
  // After the user renames a linked line, the dictionary entry that new name is, if any.
  const [nameEdited, setNameEdited] = useState(false);
  const [named, setNamed] = useState<{ name: string; id: number; entry: string } | null>(null);
  const [kept, setKept] = useState<string | null>(null);

  useEffect(() => {
    if (!nameEdited || line.ingredient_id === null || !line.name.trim()) return;
    const name = line.name;
    const timer = setTimeout(() => {
      api
        .searchIngredients(name)
        .then((entries) => {
          const exact = entries.find((entry) => namesEntry(entry, name));
          setNamed(exact ? { name, id: exact.id, entry: entryName(exact, locale) } : null);
        })
        .catch(() => {});
    }, 400);
    return () => clearTimeout(timer);
  }, [nameEdited, line.name, line.ingredient_id, locale]);

  // A new name that is another entry most likely means the link is now wrong.
  const conflict =
    named !== null &&
    named.name === line.name &&
    line.ingredient_id !== null &&
    named.id !== line.ingredient_id &&
    kept !== line.name
      ? named
      : null;

  function update(fields: Partial<ReviewLine>) {
    onChange({ ...line, ...fields });
  }

  function link(id: number, name: string, byHand = false) {
    update({
      ingredient_id: id,
      linkedName: name,
      skipped: false,
      touched: true,
      // Relinking a line whose own name matched is a correction, not a new wording.
      linkedByHand: byHand && !line.startedMatched,
      rememberLink: true,
    });
    setPrevious(null);
    setMode("idle");
  }

  // 适量 and 没写 are two ways to say there is no number; each one clears the number and unit.
  function toggleNoNumber(type: "vague" | "missing") {
    if (line.amount_type === type) {
      // Released: the amount stays empty and neither toggle is pressed.
      update({ amount_type: typeForUnit(null) });
    } else {
      update({ amount_type: type, amount: null, unit: null });
    }
  }

  const problem = amountProblem(line);
  const state =
    line.ingredient_id !== null ? "matched" : line.skipped ? "skipped" : "unmatched";

  return (
    <li
      data-testid={`row-${line.name}`}
      className={`line ${state}`}
      onDragOver={(event) => {
        if (dragging) event.preventDefault();
      }}
      onDrop={(event) => {
        // Only a line drag is ours; text dragged into an input keeps the browser default.
        if (!dragging) return;
        event.preventDefault();
        // Handled here; the list background's drop (leave the choice) must not also fire.
        event.stopPropagation();
        onDropLine();
      }}
    >
      <div className="line-raw">
        <span>
        <span
          className="drag-handle"
          aria-label={dragLabel}
          title={dragLabel}
          draggable
          onDragStart={(event) => {
            // Firefox starts an HTML5 drag only when the event carries data; the custom
            // type also marks this drag as ours.
            event.dataTransfer.setData("application/x-mmd-line", String(line.key));
            event.dataTransfer.effectAllowed = "move";
            onDragStart();
          }}
          onDragEnd={onDragEnd}
        >
          ⠿ {t("drag")}
        </span>{" "}
        {t("raw")}: {line.added ? t("addedByYou") : line.raw}
      </span>
        <span className="line-actions">
          {onUngroup && (
            <button type="button" className="compact" onClick={onUngroup}>
              {t("choiceOut")}
            </button>
          )}
          <button type="button" className="btn-danger compact" onClick={onRemove}>
            {t("remove")}
          </button>
        </span>
      </div>
      <div className="line-fields line-grid">
        <label className="mini-field" data-slot="name">
          {t("name")}
          <input
            value={line.name}
            onChange={(event) => {
              setNameEdited(true);
              update({ name: event.target.value, touched: true });
            }}
          />
        </label>
        <label className="mini-field" data-slot="amount">
          {t("amount")}
          <input
            type="number"
            step="any"
            min="0"
            // A wheel turn over a focused number field would change the amount unseen.
            onWheel={(event) => event.currentTarget.blur()}
            value={line.amount ?? ""}
            onChange={(event) => {
              const amount = event.target.value === "" ? null : Number(event.target.value);
              // A number releases a pressed toggle; the type then follows the unit.
              update({ amount, amount_type: typeForUnit(line.unit) });
            }}
          />
        </label>
        <span className="unit-wrap" data-slot="unit">
        <label className="mini-field">
          {t("unit")}
          <select
            // A unit means nothing without a number, and the save drops it.
            disabled={line.amount === null}
            value={line.unit ?? ""}
            onChange={(event) => {
              const unit = (event.target.value || null) as Unit | null;
              update({ unit, amount_type: typeForUnit(unit) });
            }}
          >
            <option
              value=""
              title={line.amount === null ? undefined : tReview("unitHouseholdTitle")}
            >
              {line.amount === null ? tUnit("none") : tReview("unitHousehold")}
            </option>
            {UNITS.map((value) => (
              <option key={value} value={value}>
                {tUnit(value)}
              </option>
            ))}
          </select>
        </label>
        {line.amount !== null && line.unit === null && (
          <small className="unit-hint">{tReview("unitHouseholdHint")}</small>
        )}
        </span>
        <div className="mini-field" data-slot="toggles">
          {t("amountMode")}
        <div className="segmented" role="group" aria-label={t("amountMode")}>
          <button
            type="button"
            aria-pressed={line.amount_type === "vague"}
            onClick={() => toggleNoNumber("vague")}
          >
            {tReview("amountVague")}
          </button>
          <button
            type="button"
            aria-pressed={line.amount_type === "missing"}
            onClick={() => toggleNoNumber("missing")}
          >
            {tReview("amountMissing")}
          </button>
        </div>
        </div>
        {/* Always rendered, so the columns after it keep their place when there is no number. */}
        <span className="mini-field amount-kind" data-slot="type">
          {line.amount !== null && (
            <>
              {t("kind")}
              <strong>{tAmount(typeForUnit(line.unit))}</strong>
            </>
          )}
        </span>
        <label className="mini-field" data-slot="role">
          {t("role")}
          <select value={line.role} onChange={(event) => update({ role: event.target.value as Role })}>
            {ROLES.map((value) => (
              <option key={value} value={value}>
                {tRole(value)}
              </option>
            ))}
          </select>
        </label>
        {grouped ? (
          <span data-slot="optional" />
        ) : (
          <label className="mini-field" data-slot="optional">
            {t("optional")}
            <input
              type="checkbox"
              checked={line.optional}
              onChange={(event) => update({ optional: event.target.checked })}
            />
          </label>
        )}
      </div>

      {problem && <p className="notice notice-warning">{t("zeroAmount")}</p>}
      <div className="line-status">
        {line.ingredient_id !== null ? (
          <>
            {t("matched", { name: line.linkedName ?? line.name })}{" "}
            <span className="dish-toggles">
              <button
                type="button"
                onClick={() => {
                  const name = line.linkedName ?? line.name;
                  setPrevious({ id: line.ingredient_id as number, name });
                  update({ ingredient_id: null, linkedName: null, touched: true, linkedByHand: false });
                }}
              >
                {t("unlink")}
              </button>
            </span>
            {line.linkedByHand && (
              <label className="mini-field inline">
                <input
                  type="checkbox"
                  checked={line.rememberLink}
                  onChange={(event) => update({ rememberLink: event.target.checked })}
                />
                {tReview("rememberLink")}
              </label>
            )}
          </>
        ) : line.skipped ? (
          <>
            {t("skipped")}{" "}
            <span className="dish-toggles">
              <button type="button" onClick={() => update({ skipped: false, touched: true })}>
                {t("undoSkip")}
              </button>
            </span>
          </>
        ) : (
          <>
            <strong>{t("unmatched")}</strong>
            <span className="dish-toggles">
              {previous && (
                <button type="button" onClick={() => link(previous.id, previous.name)}>
                  {t("undoUnlink")}
                </button>
              )}
              <button
                type="button"
                aria-expanded={mode === "link"}
                onClick={() => setMode(mode === "link" ? "idle" : "link")}
              >
                {t("linkExisting")}<span aria-hidden="true"> {mode === "link" ? "▴" : "▾"}</span>
              </button>
              <button
                type="button"
                aria-expanded={mode === "create"}
                onClick={() => setMode(mode === "create" ? "idle" : "create")}
              >
                {t("createEntry")}<span aria-hidden="true"> {mode === "create" ? "▴" : "▾"}</span>
              </button>
              <button
                type="button"
                onClick={() => {
                  setPrevious(null);
                  update({ skipped: true, touched: true });
                }}
              >
                {t("skip")}
              </button>
            </span>
          </>
        )}
      </div>
      {conflict && (
        <div className="notice notice-warning actions">
          <span>
            {t("nameConflict", {
              name: line.name,
              entry: conflict.entry,
              linked: line.linkedName ?? line.name,
            })}
          </span>
          <button type="button" onClick={() => link(conflict.id, conflict.entry)}>
            {t("relinkTo", { entry: conflict.entry })}
          </button>
          <button type="button" onClick={() => setKept(line.name)}>
            {t("keepLink", { linked: line.linkedName ?? line.name })}
          </button>
        </div>
      )}
      {mode === "link" && line.ingredient_id === null && (
        <div className="line-panel">
          <DictionarySearch
            search={api.searchIngredients}
            onChoose={(entry) => link(entry.id, entryName(entry, locale), true)}
            onCancel={() => setMode("idle")}
          />
        </div>
      )}
      {mode === "create" && line.ingredient_id === null && (
        <div className="line-panel">
          <CreateIngredient
            line={line}
            lang={lang}
            onCreated={link}
            onCancel={() => setMode("idle")}
          />
        </div>
      )}
    </li>
  );
}
