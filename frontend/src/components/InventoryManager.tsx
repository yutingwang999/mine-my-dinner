"use client";

import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";

import { DictionarySearch } from "@/components/DictionarySearch";
import {
  api,
  type DictionaryEntry,
  entryName,
  type EntryRef,
  type IngredientEntry,
  type InventoryRow,
  STOCK_LEVELS,
  type StockLevel,
} from "@/lib/api";
import { useErrorText } from "@/lib/errors";
import { useLoad } from "@/lib/useLoad";

function asEntry(ref: EntryRef | InventoryRow) {
  const id = "ingredient_id" in ref ? ref.ingredient_id : ref.id;
  const entry: DictionaryEntry = {
    id,
    name_zh: ref.name_zh,
    name_en: ref.name_en,
    aliases_zh: [],
    aliases_en: [],
    source: "",
  };
  return entry;
}

/** The add form shows only the fields the entry's stock_mode allows. */
function AddInventory({ onAdded }: { onAdded: () => void }) {
  const t = useTranslations("inventory");
  const tLevel = useTranslations("level");
  const errorText = useErrorText();
  const locale = useLocale();
  const [entry, setEntry] = useState<IngredientEntry | null>(null);
  const [count, setCount] = useState("");
  const [level, setLevel] = useState<StockLevel>("plenty");
  const [grams, setGrams] = useState("");
  const [expiresOn, setExpiresOn] = useState("");
  const [error, setError] = useState<unknown>(null);

  async function add() {
    if (entry === null) return;
    setError(null);
    try {
      await api.putInventory({
        ingredient_id: entry.id,
        count: entry.stock_mode === "count" && count !== "" ? Number(count) : null,
        level: entry.stock_mode === "level" ? level : null,
        grams: entry.stock_mode === "level" && grams !== "" ? Number(grams) : null,
        expires_on: expiresOn || null,
      });
      setEntry(null);
      setCount("");
      setGrams("");
      setExpiresOn("");
      onAdded();
    } catch (caught) {
      setError(caught);
    }
  }

  return (
    <div>
      <DictionarySearch search={api.searchIngredients} onChoose={setEntry} onCancel={() => setEntry(null)} />
      {entry !== null && (
        <div className="line-fields">
          <strong>{entryName(entry, locale)}</strong>
          {entry.stock_mode === "count" && (
            <label className="mini-field">
              {t("count")}
              <input type="number" min="0" value={count} onChange={(event) => setCount(event.target.value)} />
            </label>
          )}
          {entry.stock_mode === "level" && (
            <>
              <label className="mini-field">
                {t("level")}
                <select value={level} onChange={(event) => setLevel(event.target.value as StockLevel)}>
                  {STOCK_LEVELS.map((value) => (
                    <option key={value} value={value}>
                      {tLevel(value)}
                    </option>
                  ))}
                </select>
              </label>
              <label className="mini-field">
                {t("grams")}
                <input type="number" min="0" value={grams} onChange={(event) => setGrams(event.target.value)} />
              </label>
            </>
          )}
          {entry.stock_mode === "availability" && <p className="hint">{t("availabilityHint")}</p>}
          <label className="mini-field">
            {t("expiresOn")}
            <input type="date" value={expiresOn} onChange={(event) => setExpiresOn(event.target.value)} />
          </label>
          <button type="button" onClick={add}>
            {t("add")}
          </button>
          {error !== null && (
            <p role="alert" className="notice notice-warning">
              {errorText(error)}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function RefList({
  title,
  hint,
  testId,
  load,
  search,
  add,
  remove,
}: {
  title: string;
  hint?: string;
  testId: string;
  load: () => Promise<EntryRef[]>;
  search: (q: string) => Promise<DictionaryEntry[]>;
  add: (id: number) => Promise<unknown>;
  remove: (id: number) => Promise<unknown>;
}) {
  const tCommon = useTranslations("common");
  const errorText = useErrorText();
  const locale = useLocale();
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const { data, reload } = useLoad(load, []);

  async function run(action: () => Promise<unknown>) {
    setError(null);
    try {
      await action();
      reload();
    } catch (caught) {
      setError(caught);
    }
  }

  return (
    <section className="card">
      <h2>{title}</h2>
      {hint && <p className="hint">{hint}</p>}
      {data?.length === 0 && <p className="hint">{tCommon("none")}</p>}
      <ul className="row-list">
        {data?.map((item) => (
          <li key={item.id} data-testid={`${testId}-${item.id}`}>
            <span>{entryName(asEntry(item), locale)}</span>
            <button type="button" onClick={() => run(() => remove(item.id))}>
              {tCommon("remove")}
            </button>
          </li>
        ))}
      </ul>
      {adding ? (
        <DictionarySearch
          search={search}
          onChoose={(entry) => run(() => add(entry.id)).then(() => setAdding(false))}
          onCancel={() => setAdding(false)}
        />
      ) : (
        <div className="actions">
          <button type="button" onClick={() => setAdding(true)}>
            {tCommon("add")}
          </button>
        </div>
      )}
      {error !== null && (
        <p role="alert" className="notice notice-warning">
          {errorText(error)}
        </p>
      )}
    </section>
  );
}

export function InventoryManager() {
  const t = useTranslations("inventory");
  const tCommon = useTranslations("common");
  const tLevel = useTranslations("level");
  const errorText = useErrorText();
  const locale = useLocale();
  const [sort, setSort] = useState<"name" | "expiry">("name");
  const [error, setError] = useState<unknown>(null);
  const { data, reload } = useLoad(() => api.listInventory(sort), [sort]);

  function amount(row: InventoryRow): string {
    if (row.stock_mode === "count") return String(row.count ?? "");
    if (row.stock_mode === "level" && row.level) {
      return row.grams !== null ? `${tLevel(row.level)} (${row.grams} g)` : tLevel(row.level);
    }
    return "";
  }

  return (
    <div>
      <section className="card">
        <div className="actions">
          <h2>{t("title")}</h2>
          <button type="button" onClick={() => setSort(sort === "name" ? "expiry" : "name")}>
            {sort === "name" ? t("sortByExpiry") : t("sortByName")}
          </button>
        </div>
        {data?.length === 0 && <p className="hint">{tCommon("none")}</p>}
        <ul className="row-list">
          {data?.map((row) => (
            <li key={row.ingredient_id} data-testid={`inventory-${row.ingredient_id}`}>
              <span className="row-main">
                <strong>{entryName(asEntry(row), locale)}</strong>
                <span className="hint">
                  {amount(row)}
                  {row.expires_on && ` · ${t("expires", { date: row.expires_on })}`}
                </span>
              </span>
              <button
                type="button"
                onClick={async () => {
                  try {
                    await api.deleteInventory(row.ingredient_id);
                    reload();
                  } catch (caught) {
                    setError(caught);
                  }
                }}
              >
                {tCommon("remove")}
              </button>
            </li>
          ))}
        </ul>
        {error !== null && (
          <p role="alert" className="notice notice-warning">
            {errorText(error)}
          </p>
        )}
        <AddInventory onAdded={reload} />
      </section>
      <RefList
        title={t("alwaysInStock")}
        hint={t("alwaysInStockHint")}
        testId="always"
        load={api.listAlwaysInStock}
        search={api.searchIngredients}
        add={api.addAlwaysInStock}
        remove={api.removeAlwaysInStock}
      />
      <RefList
        title={t("cookwareOwned")}
        testId="owned"
        load={api.listOwnedCookware}
        search={api.searchCookware}
        add={api.addOwnedCookware}
        remove={api.removeOwnedCookware}
      />
    </div>
  );
}
