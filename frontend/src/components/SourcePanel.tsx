"use client";

import { useTranslations } from "next-intl";

import { imageUrl, type SavedRecipe } from "@/lib/api";

/** What the import kept of the original: the source link, the pasted text, and screenshots. */
export function SourcePanel({ recipe }: { recipe: SavedRecipe }) {
  const t = useTranslations("recipeDetail");
  const tLibrary = useTranslations("library");

  const hasLink = Boolean(recipe.source_url);
  const hasText = Boolean(recipe.source_text);
  const hasImages = recipe.images.length > 0;
  if (!hasLink && !hasText && !hasImages) {
    return <p className="hint">{t("noOriginalText")}</p>;
  }
  return (
    <div className="source-panel">
      {hasLink && (
        <p>
          {t("source")}:{" "}
          <a className="external-link" href={recipe.source_url ?? undefined} target="_blank" rel="noreferrer">
            {recipe.source_url}
            <span aria-hidden="true"> ↗</span>
          </a>
        </p>
      )}
      {hasText && <pre className="source-text">{recipe.source_text}</pre>}
      {hasImages && (
        <div className="screenshots">
          {recipe.images.map((image, index) => (
            <a key={image.file_name} href={imageUrl(image.file_name)} target="_blank" rel="noreferrer">
              {/* eslint-disable-next-line @next/next/no-img-element -- local files */}
              <img src={imageUrl(image.file_name)} alt={tLibrary("screenshotAlt", { number: index + 1 })} />
            </a>
          ))}
        </div>
      )}
    </div>
  );
}
