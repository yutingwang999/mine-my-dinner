"use client";

import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";

import { RecipeDetail } from "@/components/RecipeDetail";
import { SourcePanel } from "@/components/SourcePanel";
import { api, type SavedRecipe } from "@/lib/api";

type Props = {
  recipeId: number;
  /** The recipe when the caller already has it; otherwise it is fetched on the first open. */
  recipe?: SavedRecipe;
  /** The dish title (a link or plain text), shown at the left of the toggles. */
  title: ReactNode;
  /** Ingredient names the plan marks as missing. */
  missing?: string[];
  /** Shown between the title row and the open panel. */
  children?: ReactNode;
  /** Extra buttons after the toggles, such as the plan card's remove button. */
  actions?: ReactNode;
};

/** A dish row: its title, the "how to cook" and "original text" toggles, and the open panel. */
export function RecipeToggles({ recipeId, recipe: given, title, missing = [], children, actions }: Props) {
  const t = useTranslations("recipeDetail");
  const [panel, setPanel] = useState<"recipe" | "source" | null>(null);
  const [fetched, setFetched] = useState<SavedRecipe | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const recipe = given ?? fetched;

  async function load() {
    setLoading(true);
    setFailed(false);
    try {
      setFetched(await api.getRecipe(recipeId));
    } catch {
      setFailed(true);
    } finally {
      setLoading(false);
    }
  }

  function toggle(target: "recipe" | "source") {
    const next = panel === target ? null : target;
    setPanel(next);
    // The recipe is fetched on the first open of either panel, then kept.
    if (next !== null && recipe === null && !loading) void load();
  }

  return (
    <>
      <div className="dish-head">
        {title}
        <span className="dish-toggles">
          <button type="button" aria-expanded={panel === "recipe"} onClick={() => toggle("recipe")}>
            {t("howToCook")} <span aria-hidden="true">{panel === "recipe" ? "▴" : "▾"}</span>
          </button>
          <button type="button" aria-expanded={panel === "source"} onClick={() => toggle("source")}>
            {t("originalText")} <span aria-hidden="true">{panel === "source" ? "▴" : "▾"}</span>
          </button>
          {actions}
        </span>
      </div>
      {children}
      {panel !== null && loading && <p className="hint">{t("loading")}</p>}
      {panel !== null && failed && (
        <p role="alert" className="notice notice-warning">
          {t("failed")}{" "}
          <button type="button" onClick={() => void load()}>
            {t("retry")}
          </button>
        </p>
      )}
      {panel !== null && recipe !== null && (
        <div className="dish-panel">
          {panel === "recipe" ? (
            <RecipeDetail recipe={recipe} compact missing={missing} />
          ) : (
            <SourcePanel recipe={recipe} />
          )}
        </div>
      )}
    </>
  );
}
