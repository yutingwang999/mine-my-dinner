"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";

import { ConfirmDelete } from "@/components/ConfirmDelete";
import { Pager } from "@/components/Pager";
import { ReviewForm } from "@/components/ReviewForm";
import { api, DISH_TYPES, imageUrl, type SavedRecipe } from "@/lib/api";
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
  const [openSource, setOpenSource] = useState<number | null>(null);
  const { data, error, reload } = useLoad(
    () => api.listRecipes({ q, dish_type: dishType, page }),
    [q, dishType, page],
  );

  if (editing !== null) {
    return (
      <section>
        <h2>{t("editTitle", { title: editing.title })}</h2>
        <ReviewForm
          // Saved screenshots stay on the server; an edit uploads nothing new.
          draft={{ ...editing, provenance: null, images: undefined }}
          save={(write) => api.updateRecipe(editing.id, { ...write, provenance: null })}
          onSaved={() => {
            setEditing(null);
            reload();
          }}
          actions={
            <button type="button" onClick={() => setEditing(null)}>
              {tCommon("cancel")}
            </button>
          }
        />
      </section>
    );
  }

  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;

  return (
    <section className="card">
      <div className="browse-filters">
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
      </div>
      {error !== null && (
        <p role="alert" className="notice notice-warning">
          {errorText(error)}
        </p>
      )}
      {data === null && error === null && <p>{tCommon("loading")}</p>}
      {data?.items.length === 0 && <p className="hint">{t("empty")}</p>}
      <ul className="row-list">
        {data?.items.map((recipe) => (
          <li key={recipe.id} data-testid={`recipe-${recipe.id}`}>
            <span className="row-main">
              <strong>{recipe.title}</strong>
              <span className="hint">
                {tDish(recipe.dish_type)}
                {recipe.tags.length > 0 && ` · ${recipe.tags.join(", ")}`}
              </span>
              {recipe.source_url && (
                <a
                  className="external-link"
                  href={recipe.source_url}
                  target="_blank"
                  rel="noreferrer"
                >
                  {t("source")}
                  <span aria-hidden="true"> ↗</span>
                </a>
              )}
              {recipe.supplies.length > 0 && (
                <small className="hint">{t("supplies", { names: recipe.supplies.join(", ") })}</small>
              )}
              {!recipe.embedded &&<small className="hint">{t("noEmbedding")}</small>}
            </span>
            <span className="actions">
              {(recipe.source_text || recipe.images.length > 0) && (
                <button
                  type="button"
                  aria-expanded={openSource === recipe.id}
                  onClick={() => setOpenSource(openSource === recipe.id ? null : recipe.id)}
                >
                  {openSource === recipe.id ? t("hideSource") : t("showSource")}
                </button>
              )}
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
            </span>
            {openSource === recipe.id && (
              <div className="line-panel">
                {recipe.source_text && <pre className="source-text">{recipe.source_text}</pre>}
                {recipe.images.length > 0 && (
                  <div className="screenshots">
                    {recipe.images.map((image, index) => (
                      <a key={image.file_name} href={imageUrl(image.file_name)} target="_blank" rel="noreferrer">
                        {/* eslint-disable-next-line @next/next/no-img-element -- local files */}
                        <img
                          src={imageUrl(image.file_name)}
                          alt={t("screenshotAlt", { number: index + 1 })}
                        />
                      </a>
                    ))}
                  </div>
                )}
              </div>
            )}
          </li>
        ))}
      </ul>
      <Pager page={page} pages={pages} onPage={setPage} />
    </section>
  );
}
