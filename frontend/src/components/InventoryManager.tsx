"use client";

import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";

import { DictionarySearch } from "@/components/DictionarySearch";
import { PagedList } from "@/components/PagedList";
import { TabPanel, Tabs } from "@/components/Tabs";
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
import { useUrlTab } from "@/lib/useUrlTab";

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
  list,
  anchor,
  items,
  reload,
  search,
  add,
  remove,
}: {
  title: string;
  hint?: string;
  testId: string;
  /** The page-size key: the choice is kept under `pageSize:<list>`. */
  list: string;
  anchor?: string;
  items: EntryRef[] | null;
  reload: () => void;
  search: (q: string) => Promise<DictionaryEntry[]>;
  add: (id: number) => Promise<unknown>;
  remove: (id: number) => Promise<unknown>;
}) {
  const tCommon = useTranslations("common");
  const errorText = useErrorText();
  const locale = useLocale();
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const data = items;

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
    <section className="card" id={anchor}>
      <h2>{title}</h2>
      {hint && <p className="hint">{hint}</p>}
      {data?.length === 0 && <p className="hint">{tCommon("none")}</p>}
      <PagedList items={data ?? []} list={list}>
        {(visible) =>
          visible.map((item) => (
            <li key={item.id} data-testid={`${testId}-${item.id}`}>
              <span>{entryName(asEntry(item), locale)}</span>
              <button type="button" onClick={() => run(() => remove(item.id))}>
                {tCommon("remove")}
              </button>
            </li>
          ))
        }
      </PagedList>
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

const TABS = ["stock", "always", "cookware"] as const;
type Tab = (typeof TABS)[number];

export function InventoryManager() {
  const t = useTranslations("inventory");
  const tCommon = useTranslations("common");
  const tLevel = useTranslations("level");
  const errorText = useErrorText();
  const locale = useLocale();
  const [sort, setSort] = useState<"name" | "expiry">("name");
  const [error, setError] = useState<unknown>(null);
  const { data, reload } = useLoad(() => api.listInventory(sort), [sort]);
  const always = useLoad(api.listAlwaysInStock, []);
  const owned = useLoad(api.listOwnedCookware, []);
  // The plan card used to link to #cookware, so that fragment still opens the cookware tab.
  const { chosen, choose } = useUrlTab(TABS, "/inventory", { cookware: "cookware" });
  const tab: Tab = chosen ?? "stock";
  const tabLabels: Record<Tab, string> = {
    stock: t("tabStock", { count: data?.length ?? 0 }),
    always: t("tabAlways", { count: always.data?.length ?? 0 }),
    cookware: t("tabCookware", { count: owned.data?.length ?? 0 }),
  };

  function amount(row: InventoryRow): string {
    if (row.stock_mode === "count") return String(row.count ?? "");
    if (row.stock_mode === "level" && row.level) {
      return row.grams !== null ? `${tLevel(row.level)} (${row.grams} g)` : tLevel(row.level);
    }
    return "";
  }

  return (
    <div>
      <Tabs
        tabs={TABS.map((value) => ({ value, label: tabLabels[value] }))}
        active={tab}
        onChange={choose}
        label={t("tabsLabel")}
        idPrefix="inventory"
      />
      <TabPanel idPrefix="inventory" active={tab}>
        {tab === "stock" && (
        <section className="card">
          <div className="actions">
            <h2>{t("title")}</h2>
            <span className="segmented-field">
              <span id="inventory-sort-label">{t("sortLabel")}</span>
              <span className="segmented" role="group" aria-labelledby="inventory-sort-label">
                {(["name", "expiry"] as const).map((value) => (
                  <button
                    key={value}
                    type="button"
                    aria-pressed={sort === value}
                    onClick={() => setSort(value)}
                  >
                    {t(value === "name" ? "sortName" : "sortExpiry")}
                  </button>
                ))}
              </span>
            </span>
          </div>
          {data?.length === 0 && <p className="hint">{tCommon("none")}</p>}
          <PagedList items={data ?? []} list="inventory">
            {(visible) =>
              visible.map((row) => (
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
              ))
            }
          </PagedList>
          {error !== null && (
            <p role="alert" className="notice notice-warning">
              {errorText(error)}
            </p>
          )}
          <AddInventory onAdded={reload} />
        </section>
        )}
        {tab === "always" && (
        <RefList
          title={t("alwaysInStock")}
          hint={t("alwaysInStockHint")}
          testId="always"
          list="always-in-stock"
          items={always.data}
          reload={always.reload}
          search={api.searchIngredients}
          add={api.addAlwaysInStock}
          remove={api.removeAlwaysInStock}
        />
        )}
        {tab === "cookware" && (
        <RefList
          title={t("cookwareOwned")}
          testId="owned"
          list="owned-cookware"
          anchor="cookware"
          items={owned.data}
          reload={owned.reload}
          search={api.searchCookware}
          add={api.addOwnedCookware}
          remove={api.removeOwnedCookware}
        />
        )}
      </TabPanel>
    </div>
  );
}
