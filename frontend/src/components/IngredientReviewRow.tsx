"use client";

import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";

import { AllergenPicker } from "@/components/AllergenPicker";
import { DictionarySearch } from "@/components/DictionarySearch";
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
import { useErrorText } from "@/lib/errors";

export type ReviewLine = IngredientLine & {
  key: number;
  skipped: boolean;
  linkedName: string | null;
};

type Props = {
  line: ReviewLine;
  lang: Lang;
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
      <label>
        {t("nameZh")}
        <input value={nameZh} onChange={(event) => setNameZh(event.target.value)} />
      </label>
      <label>
        {t("nameEn")}
        <input value={nameEn} onChange={(event) => setNameEn(event.target.value)} />
      </label>
      <label>
        {t("category")}
        <select value={category} onChange={(event) => setCategory(event.target.value as Category)}>
          {CATEGORIES.map((value) => (
            <option key={value} value={value}>
              {tCategory(value)}
            </option>
          ))}
        </select>
      </label>
      <label>
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

export function IngredientReviewRow({ line, lang, onChange, onRemove }: Props) {
  const t = useTranslations("line");
  const tUnit = useTranslations("unit");
  const tAmount = useTranslations("amountType");
  const tRole = useTranslations("role");
  const locale = useLocale();
  const [mode, setMode] = useState<Mode>("idle");

  function update(fields: Partial<ReviewLine>) {
    onChange({ ...line, ...fields });
  }

  function link(id: number, name: string) {
    update({ ingredient_id: id, linkedName: name, skipped: false });
    setMode("idle");
  }

  function setAmountType(amountType: AmountType) {
    // A vague or missing amount never carries a number.
    const clears = amountType === "vague" || amountType === "missing";
    update({
      amount_type: amountType,
      ...(clears ? { amount: null, unit: null } : {}),
    });
  }

  const noNumber = line.amount_type === "vague" || line.amount_type === "missing";

  return (
    <li data-testid={`row-${line.name}`} style={{ marginBottom: "0.75rem" }}>
      <div>
        {t("raw")}: {line.raw}
      </div>
      <label>
        {t("name")}
        <input value={line.name} onChange={(event) => update({ name: event.target.value })} />
      </label>
      <label>
        {t("amount")}
        <input
          type="number"
          step="any"
          min="0"
          disabled={noNumber}
          value={line.amount ?? ""}
          onChange={(event) =>
            update({ amount: event.target.value === "" ? null : Number(event.target.value) })
          }
        />
      </label>
      <label>
        {t("unit")}
        <select
          disabled={noNumber}
          value={line.unit ?? ""}
          onChange={(event) => update({ unit: (event.target.value || null) as Unit | null })}
        >
          <option value="">{tUnit("none")}</option>
          {UNITS.map((value) => (
            <option key={value} value={value}>
              {tUnit(value)}
            </option>
          ))}
        </select>
      </label>
      <label>
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
      <label>
        {t("role")}
        <select value={line.role} onChange={(event) => update({ role: event.target.value as Role })}>
          {ROLES.map((value) => (
            <option key={value} value={value}>
              {tRole(value)}
            </option>
          ))}
        </select>
      </label>
      <label>
        <input
          type="checkbox"
          checked={line.optional}
          onChange={(event) => update({ optional: event.target.checked })}
        />
        {t("optional")}
      </label>
      <button type="button" onClick={onRemove}>
        {t("remove")}
      </button>

      <div>
        {line.ingredient_id !== null ? (
          <>
            {t("matched", { name: line.linkedName ?? line.name })}{" "}
            <button type="button" onClick={() => update({ ingredient_id: null, linkedName: null })}>
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
            {t("unmatched")}{" "}
            <button type="button" onClick={() => setMode("link")}>
              {t("linkExisting")}
            </button>
            <button type="button" onClick={() => setMode("create")}>
              {t("createEntry")}
            </button>
            <button type="button" onClick={() => update({ skipped: true })}>
              {t("skip")}
            </button>
          </>
        )}
      </div>
      {mode === "link" && line.ingredient_id === null && (
        <DictionarySearch
          search={api.searchIngredients}
          onChoose={(entry) => link(entry.id, entryName(entry, locale))}
          onCancel={() => setMode("idle")}
        />
      )}
      {mode === "create" && line.ingredient_id === null && (
        <CreateIngredient
          line={line}
          lang={lang}
          onCreated={link}
          onCancel={() => setMode("idle")}
        />
      )}
    </li>
  );
}
