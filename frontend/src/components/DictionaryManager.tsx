"use client";

import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";

import { ConfirmPanel, type LinkNotice, LinkPanel, type Target } from "@/components/AllergenResolvePanels";
import { ConfirmDelete } from "@/components/ConfirmDelete";
import { DictionaryBrowser } from "@/components/DictionaryBrowser";
import { EditIngredient } from "@/components/EditIngredient";
import { entryNames, useListSearch } from "@/components/ListSearch";
import { PagedList } from "@/components/PagedList";
import { TabPanel, Tabs } from "@/components/Tabs";
import { api, entryName, type IngredientEntry } from "@/lib/api";
import { useLoad } from "@/lib/useLoad";
import { useUrlTab } from "@/lib/useUrlTab";

function ResolveRow({
  testId,
  label,
  name,
  dishCount,
  target,
  onResolved,
  onAct,
}: {
  testId: string;
  label: string;
  name: string;
  dishCount: number;
  target: Target;
  onResolved: (notice: LinkNotice | null) => void;
  /** Called when the user starts something in this row, so an old notice can go. */
  onAct: () => void;
}) {
  const t = useTranslations("dictionary");
  // One panel at a time: the user either confirms allergens or links to an entry.
  const [panel, setPanel] = useState<"confirm" | "link" | null>(null);
  const close = () => setPanel(null);
  const open = (next: "confirm" | "link") => {
    onAct();
    setPanel(next);
  };

  return (
    <li data-testid={testId}>
      <span className="row-main">
        <strong>{label}</strong>
        <span className="hint">{t("dishCount", { count: dishCount })}</span>
      </span>
      {panel === null && (
        <span className="actions">
          <button type="button" onClick={() => open("confirm")}>
            {t("resolve")}
          </button>
          {"name" in target && (
            <button type="button" onClick={() => open("link")}>
              {t("linkExisting")}
            </button>
          )}
        </span>
      )}
      {panel === "confirm" && (
        <ConfirmPanel name={name} target={target} onCancel={close} onResolved={() => onResolved(null)} />
      )}
      {panel === "link" && "name" in target && (
        <LinkPanel name={target.name} onCancel={close} onResolved={onResolved} />
      )}
    </li>
  );
}

const TABS = ["unresolved", "mine", "my-cookware", "all", "all-cookware"] as const;
type Tab = (typeof TABS)[number];

function allergenSummary(entry: IngredientEntry, t: (key: string) => string, tAllergen: (key: string) => string) {
  if (entry.allergens === null) return t("allergensUnknown");
  if (entry.allergens.length === 0) return t("allergensNone");
  return entry.allergens.map((allergen) => tAllergen(allergen)).join(", ");
}

export function DictionaryManager() {
  const t = useTranslations("dictionary");
  const tAllergen = useTranslations("allergen");
  const tCommon = useTranslations("common");
  const tCategory = useTranslations("category");
  const locale = useLocale();
  const { chosen: chosenTab, choose: chooseTab } = useUrlTab(TABS, "/dictionary");
  const [editingId, setEditingId] = useState<number | null>(null);
  // Shown at the top of the unknown list until the user acts in it again.
  const [linkNotice, setLinkNotice] = useState<LinkNotice | null>(null);
  // Bumped after any change, so the full list below shows it too.
  const [refreshKey, setRefreshKey] = useState(0);
  const unresolved = useLoad(api.listUnresolved, []);
  const ingredients = useLoad(api.listUserIngredients, []);
  const cookware = useLoad(api.listUserCookware, []);

  function reloadAll() {
    unresolved.reload();
    ingredients.reload();
    setRefreshKey((key) => key + 1);
  }

  const unresolvedCount =
    unresolved.data === null ? 0 : unresolved.data.entries.length + unresolved.data.unmatched.length;
  // With no tab chosen, open on the unknown list when it has rows, else on the full list.
  // Until the unknown list has loaded there is nothing to decide, so no section shows yet.
  const tab: Tab | null = chosenTab ?? (unresolved.data === null ? null : unresolvedCount > 0 ? "unresolved" : "all");
  const tabLabels: Record<Tab, string> = {
    unresolved: t("tabUnresolved", { count: unresolvedCount }),
    mine: t("tabMine", { count: ingredients.data?.length ?? 0 }),
    "my-cookware": t("tabMyCookware", { count: cookware.data?.length ?? 0 }),
    all: t("tabAll"),
    "all-cookware": t("tabAllCookware"),
  };

  const nothingUnresolved =
    unresolved.data !== null &&
    unresolved.data.entries.length === 0 &&
    unresolved.data.unmatched.length === 0;

  const allUnresolvedItems = [
    ...(unresolved.data?.entries ?? []).map((entry) => ({ kind: "entry" as const, entry })),
    ...(unresolved.data?.unmatched ?? []).map((item) => ({ kind: "name" as const, item })),
  ];
  const unresolvedSearch = useListSearch(allUnresolvedItems, (row) => [
    row.kind === "entry" ? row.entry.name : row.item.name,
  ]);
  const mineSearch = useListSearch(ingredients.data ?? [], entryNames);
  const cookwareSearch = useListSearch(cookware.data ?? [], entryNames);

  return (
    <div>
      <Tabs
        tabs={TABS.map((value) => ({ value, label: tabLabels[value] }))}
        active={tab}
        onChange={chooseTab}
        label={t("tabsLabel")}
        idPrefix="dictionary"
      />
      <TabPanel idPrefix="dictionary" active={tab}>
        {tab === "unresolved" && (
          <section className="card">
            <h2>{t("unresolved")}</h2>
            {linkNotice !== null && (
              <p role="status" className="notice notice-info">
                {t("linkNotRemembered", linkNotice)}
              </p>
            )}
            <p className="hint">{t("unresolvedHint")}</p>
            {nothingUnresolved && <p>{t("allResolved")}</p>}
            {unresolvedSearch.box}
            {unresolvedSearch.empty}
            <PagedList key={unresolvedSearch.resetKey} items={unresolvedSearch.filtered} list="unresolved">
              {(visible) =>
                visible.map((row) =>
                  row.kind === "entry" ? (
                    <ResolveRow
                      key={`entry-${row.entry.id}`}
                      testId={`unresolved-entry-${row.entry.id}`}
                      label={row.entry.name}
                      name={row.entry.name}
                      dishCount={row.entry.dish_count}
                      target={{ ingredient_id: row.entry.id }}
                      onResolved={(notice) => {
                        setLinkNotice(notice);
                        reloadAll();
                      }}
                      onAct={() => setLinkNotice(null)}
                    />
                  ) : (
                    <ResolveRow
                      key={`name-${row.item.name}`}
                      testId={`unresolved-name-${row.item.name}`}
                      label={t("unmatchedName", { name: row.item.name })}
                      name={row.item.name}
                      dishCount={row.item.dish_count}
                      target={{ name: row.item.name }}
                      onResolved={(notice) => {
                        setLinkNotice(notice);
                        reloadAll();
                      }}
                      onAct={() => setLinkNotice(null)}
                    />
                  ),
                )
              }
            </PagedList>
          </section>
        )}

        {tab === "mine" && (
          <section className="card">
            <h2>{t("userIngredients")}</h2>
            <p className="hint">{t("seedNotice")}</p>
            {ingredients.data?.length === 0 && <p className="hint">{tCommon("none")}</p>}
            {mineSearch.box}
            {mineSearch.empty}
            <PagedList key={mineSearch.resetKey} items={mineSearch.filtered} list="my-ingredients">
              {(visible) =>
                visible.map((entry) => (
                  <li key={entry.id} data-testid={`user-ingredient-${entry.id}`}>
                    <span className="row-main">
                      <strong>{entryName(entry, locale)}</strong>
                      <span className="hint">
                        {tCategory(entry.category)} · {allergenSummary(entry, t, tAllergen)}
                        {!!entry.dish_count && ` · ${t("dishCount", { count: entry.dish_count })}`}
                      </span>
                    </span>
                    <span className="actions">
                      {editingId !== entry.id && (
                        <button type="button" onClick={() => setEditingId(entry.id)}>
                          {tCommon("edit")}
                        </button>
                      )}
                      <ConfirmDelete
                        name={entryName(entry, locale)}
                        notice={
                          entry.dish_count
                            ? t("deleteIngredientNotice", { count: entry.dish_count })
                            : undefined
                        }
                        onDelete={async () => {
                          await api.deleteIngredient(entry.id);
                          reloadAll();
                        }}
                      />
                    </span>
                    {editingId === entry.id && (
                      <EditIngredient
                        entry={entry}
                        onSaved={() => {
                          setEditingId(null);
                          reloadAll();
                        }}
                        onCancel={() => setEditingId(null)}
                      />
                    )}
                  </li>
                ))
              }
            </PagedList>
          </section>
        )}

        {tab === "my-cookware" && (
          <section className="card">
            <h2>{t("userCookware")}</h2>
            <p className="hint">{t("userCookwareHint")}</p>
            {cookware.data?.length === 0 && <p className="hint">{tCommon("none")}</p>}
            {cookwareSearch.box}
            {cookwareSearch.empty}
            <PagedList key={cookwareSearch.resetKey} items={cookwareSearch.filtered} list="my-cookware">
              {(visible) =>
                visible.map((entry) => (
                  <li key={entry.id} data-testid={`user-cookware-${entry.id}`}>
                    <span className="row-main">
                      <strong>{entryName(entry, locale)}</strong>
                      {!!entry.dish_count && (
                        <span className="hint">{t("dishCount", { count: entry.dish_count })}</span>
                      )}
                    </span>
                    <ConfirmDelete
                      name={entryName(entry, locale)}
                      notice={
                        entry.dish_count
                          ? t("deleteCookwareNotice", { count: entry.dish_count })
                          : undefined
                      }
                      onDelete={async () => {
                        await api.deleteCookware(entry.id);
                        cookware.reload();
                        setRefreshKey((key) => key + 1);
                      }}
                    />
                  </li>
                ))
              }
            </PagedList>
          </section>
        )}

        {(tab === "all" || tab === "all-cookware") && (
          <DictionaryBrowser
            refreshKey={refreshKey}
            section={tab === "all" ? "ingredients" : "cookware"}
          />
        )}
      </TabPanel>
    </div>
  );
}
