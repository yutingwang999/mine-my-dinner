"use client";

import { useLocale, useTranslations } from "next-intl";
import { useEffect, useState } from "react";

import { AllergenPicker } from "@/components/AllergenPicker";
import { DictionarySearch, namesEntry } from "@/components/DictionarySearch";
import {
  type Allergen,
  AMOUNT_TYPES,
  type AmountType,
  api,
  ApiError,
  CATEGORIES,
  type Category,
  entryName,
  type IngredientLine,
  type Lang,
  ROLES,
  type Role,
  STOCK_MODES,
  type StockMode,
  type Unit,
  UNITS,
} from "@/lib/api";
import { amountProblem, typeForUnit } from "@/lib/amounts";
import { useErrorText } from "@/lib/errors";

export type ReviewLine = IngredientLine & {
  key: number;
  skipped: boolean;
  linkedName: string | null;
  /** Added on the review page, so it has no source wording of its own. */
  added: boolean;
};

type Props = {
  line: ReviewLine;
  lang: Lang;
  /** The drag handle's accessible name, such as "拖动酸奶". */
  dragLabel: string;
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
  const tCategory = useTranslations("category");
  const tStock = useTranslations("stockMode");
  const errorText = useErrorText();
  const [nameZh, setNameZh] = useState(lang === "zh" ? line.name : "");
  const [nameEn, setNameEn] = useState(lang === "en" ? line.name : "");
  const [category, setCategory] = useState<Category>(line.role === "seasoning" ? "seasoning" : "other");
  const [stockMode, setStockMode] = useState<StockMode>("level");
  const [allergens, setAllergens] = useState<Allergen[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  async function create() {
    setBusy(true);
    setError(null);
    try {
      const entry = await api.createIngredient({
        name_zh: nameZh.trim() || null,
        name_en: nameEn.trim() || null,
        category,
        stock_mode: stockMode,
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
      <label className="mini-field">
        {t("nameZh")}
        <input value={nameZh} onChange={(event) => setNameZh(event.target.value)} />
      </label>
      <label className="mini-field">
        {t("nameEn")}
        <input value={nameEn} onChange={(event) => setNameEn(event.target.value)} />
      </label>
      <label className="mini-field">
        {t("category")}
        <select value={category} onChange={(event) => setCategory(event.target.value as Category)}>
          {CATEGORIES.map((value) => (
            <option key={value} value={value}>
              {tCategory(value)}
            </option>
          ))}
        </select>
      </label>
      <label className="mini-field">
        {t("stockMode")}
        <select
          value={stockMode}
          onChange={(event) => setStockMode(event.target.value as StockMode)}
        >
          {STOCK_MODES.map((value) => (
            <option key={value} value={value}>
              {tStock(value)}
            </option>
          ))}
        </select>
      </label>
      <AllergenPicker name={line.name} onChange={setAllergens} />
      <button type="button" disabled={busy} onClick={create}>
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

  function link(id: number, name: string) {
    update({ ingredient_id: id, linkedName: name, skipped: false });
    setPrevious(null);
    setMode("idle");
  }

  function setAmountType(amountType: AmountType) {
    // A vague or missing amount never carries a number.
    const clears = amountType === "vague" || amountType === "missing";
    // A unit that belongs to another type is cleared, so the user picks a fitting one.
    const unitFits = line.unit !== null && typeForUnit(line.unit) === amountType;
    update({
      amount_type: amountType,
      ...(clears ? { amount: null, unit: null } : unitFits ? {} : { unit: null }),
    });
  }

  const noNumber = line.amount_type === "vague" || line.amount_type === "missing";
  const problem = amountProblem(line);
  const state =
    line.ingredient_id !== null ? "matched" : line.skipped ? "skipped" : "unmatched";

  return (
    <li
      data-testid={`row-${line.name}`}
      className={`line ${state}`}
      onDragOver={(event) => event.preventDefault()}
      onDrop={(event) => {
        // Handled here; the list background's drop (leave the choice) must not also fire.
        event.preventDefault();
        event.stopPropagation();
        onDropLine();
      }}
    >
      <div className="line-raw">
        <span
          className="drag-handle"
          role="button"
          aria-label={dragLabel}
          title={dragLabel}
          draggable
          onDragStart={onDragStart}
          onDragEnd={onDragEnd}
        >
          ⠿ {t("drag")}
        </span>{" "}
        {t("raw")}: {line.added ? t("addedByYou") : line.raw}
      </div>
      <div className="line-fields">
        <label className="mini-field">
          {t("name")}
          <input
            value={line.name}
            onChange={(event) => {
              setNameEdited(true);
              update({ name: event.target.value });
            }}
          />
        </label>
        <label className="mini-field">
          {t("amount")}
          <input
            type="number"
            step="any"
            min="0"
            disabled={noNumber}
            // A wheel turn over a focused number field would change the amount unseen.
            onWheel={(event) => event.currentTarget.blur()}
            value={line.amount ?? ""}
            onChange={(event) =>
              update({ amount: event.target.value === "" ? null : Number(event.target.value) })
            }
          />
        </label>
        <label className="mini-field">
          {t("unit")}
          <select
            disabled={noNumber}
            value={line.unit ?? ""}
            onChange={(event) => {
              const unit = (event.target.value || null) as Unit | null;
              update({ unit, amount_type: typeForUnit(unit) });
            }}
          >
            <option value="">{tUnit("none")}</option>
            {UNITS.map((value) => (
              <option key={value} value={value}>
                {tUnit(value)}
              </option>
            ))}
          </select>
        </label>
        <label className="mini-field">
          {t("amountType")}
          <select
            value={line.amount_type}
            onChange={(event) => setAmountType(event.target.value as AmountType)}
          >
            {AMOUNT_TYPES.map((value) => (
              <option key={value} value={value}>
                {tAmount(value)}
              </option>
            ))}
          </select>
        </label>
        <label className="mini-field">
          {t("role")}
          <select value={line.role} onChange={(event) => update({ role: event.target.value as Role })}>
            {ROLES.map((value) => (
              <option key={value} value={value}>
                {tRole(value)}
              </option>
            ))}
          </select>
        </label>
        {!grouped && (
          <label className="mini-field inline">
            <input
              type="checkbox"
              checked={line.optional}
              onChange={(event) => update({ optional: event.target.checked })}
            />
            {t("optional")}
          </label>
        )}
        {onUngroup && (
          <button type="button" onClick={onUngroup}>
            {t("choiceOut")}
          </button>
        )}
        <button type="button" onClick={onRemove}>
          {t("remove")}
        </button>
      </div>

      {problem && (
        <p className="notice notice-warning">
          {problem.kind === "zero" ? (
            t("zeroAmount")
          ) : (
            <>
              {line.unit === null
                ? t("mismatchNoUnit")
                : t(problem.expected === "exact" ? "mismatchExact" : problem.expected === "count" ? "mismatchCount" : "mismatchSpoon")}{" "}
              <button type="button" onClick={() => update({ amount_type: problem.expected })}>
                {t("fixType", { type: tAmount(problem.expected) })}
              </button>
            </>
          )}
        </p>
      )}
      <div className="line-status">
        {line.ingredient_id !== null ? (
          <>
            {t("matched", { name: line.linkedName ?? line.name })}{" "}
            <button
              type="button"
              onClick={() => {
                const name = line.linkedName ?? line.name;
                setPrevious({ id: line.ingredient_id as number, name });
                update({ ingredient_id: null, linkedName: null });
              }}
            >
              {t("unlink")}
            </button>
          </>
        ) : line.skipped ? (
          <>
            {t("skipped")}{" "}
            <button type="button" onClick={() => update({ skipped: false })}>
              {t("undoSkip")}
            </button>
          </>
        ) : (
          <>
            <strong>{t("unmatched")}</strong>
            {previous && (
              <button type="button" onClick={() => link(previous.id, previous.name)}>
                {t("undoUnlink")}
              </button>
            )}
            <button type="button" onClick={() => setMode("link")}>
              {t("linkExisting")}
            </button>
            <button type="button" onClick={() => setMode("create")}>
              {t("createEntry")}
            </button>
            <button
              type="button"
              onClick={() => {
                setPrevious(null);
                update({ skipped: true });
              }}
            >
              {t("skip")}
            </button>
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
            onChoose={(entry) => link(entry.id, entryName(entry, locale))}
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
