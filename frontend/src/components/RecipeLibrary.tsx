"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";

import { ConfirmDelete } from "@/components/ConfirmDelete";
import { Pager } from "@/components/Pager";
import { ReviewForm } from "@/components/ReviewForm";
import { api, DISH_TYPES, type SavedRecipe } from "@/lib/api";
import { useErrorText } from "@/lib/errors";
import { useLoad } from "@/lib/useLoad";

const PAGE_SIZE = 20;

export function RecipeLibrary() {
  const t = useTranslations("library");
  const tCommon = useTranslations("common");
  const tDish = useTranslations("dishType");
  const errorText = useErrorText();
  const [q, setQ] = useState("");
  const [dishType, setDishType] = useState("");
  const [page, setPage] = useState(1);
  const [editing, setEditing] = useState<SavedRecipe | null>(null);
  const { data, error, reload } = useLoad(
    () => api.listRecipes({ q, dish_type: dishType, page }),
    [q, dishType, page],
  );

  if (editing !== null) {
    return (
      <section>
        <h2>{t("editTitle", { title: editing.title })}</h2>
        <ReviewForm
          draft={{ ...editing, provenance: null }}
          save={(write) => api.updateRecipe(editing.id, { ...write, provenance: null })}
          onSaved={() => {
            setEditing(null);
            reload();
          }}
        />
        <button type="button" onClick={() => setEditing(null)}>
          {tCommon("cancel")}
        </button>
      </section>
    );
  }

  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;

  return (
    <section>
      <input
        type="search"
        aria-label={t("search")}
        placeholder={t("search")}
        value={q}
        onChange={(event) => {
          setQ(event.target.value);
          setPage(1);
        }}
      />
      <label>
        {t("dishTypeFilter")}
        <select
          value={dishType}
          onChange={(event) => {
            setDishType(event.target.value);
            setPage(1);
          }}
        >
          <option value="">{t("allTypes")}</option>
          {DISH_TYPES.map((value) => (
            <option key={value} value={value}>
              {tDish(value)}
            </option>
          ))}
        </select>
      </label>
      {error !== null && <p role="alert">{errorText(error)}</p>}
      {data === null && error === null && <p>{tCommon("loading")}</p>}
      {data?.items.length === 0 && <p>{t("empty")}</p>}
      <ul>
        {data?.items.map((recipe) => (
          <li key={recipe.id} data-testid={`recipe-${recipe.id}`}>
            <strong>{recipe.title}</strong> {tDish(recipe.dish_type)}
            {recipe.tags.length > 0 && <span> · {recipe.tags.join(", ")}</span>}{" "}
            {recipe.source_url && (
              <a href={recipe.source_url} target="_blank" rel="noreferrer">
                {t("source")}
              </a>
            )}
            {!recipe.embedded && <small> {t("noEmbedding")}</small>}{" "}
            <button type="button" onClick={() => setEditing(recipe)}>
              {tCommon("edit")}
            </button>
            <ConfirmDelete
              name={recipe.title}
              notice={t("deleteNotice")}
              onDelete={async () => {
                await api.deleteRecipe(recipe.id);
                reload();
              }}
            />
          </li>
        ))}
      </ul>
      <Pager page={page} pages={pages} onPage={setPage} />
    </section>
  );
}
