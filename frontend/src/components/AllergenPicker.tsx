"use client";

import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";

import { type Allergen, ALLERGENS, api } from "@/lib/api";

type Status = "loading" | "suggested" | "none" | "unavailable";
type Confirmed = "list" | "none" | null;

type Props = {
  name: string;
  /** Null until the user confirms, which means the allergens stay unknown. */
  onChange: (allergens: Allergen[] | null) => void;
};

/**
 * Shows a model's allergen suggestion, preselected but without effect until the
 * user confirms it. An empty suggestion is never treated as "contains none".
 */
export function AllergenPicker({ name, onChange }: Props) {
  const t = useTranslations("allergens");
  const tAllergen = useTranslations("allergen");
  const [status, setStatus] = useState<Status>("loading");
  const [selected, setSelected] = useState<Allergen[]>([]);
  const [confirmed, setConfirmed] = useState<Confirmed>(null);

  useEffect(() => {
    let active = true;
    api
      .suggestAllergens(name)
      .then(({ suggested }) => {
        if (!active) return;
        if (suggested === null) {
          setStatus("unavailable");
        } else {
          setSelected(suggested);
          setStatus(suggested.length > 0 ? "suggested" : "none");
        }
      })
      .catch(() => active && setStatus("unavailable"));
    return () => {
      active = false;
    };
  }, [name]);

  function confirm(value: Confirmed, allergens: Allergen[]) {
    setConfirmed(value);
    onChange(value === "list" ? allergens : value === "none" ? [] : null);
  }

  function toggle(allergen: Allergen) {
    const next = selected.includes(allergen)
      ? selected.filter((item) => item !== allergen)
      : ALLERGENS.filter((item) => item === allergen || selected.includes(item));
    setSelected(next);
    // A changed selection needs a new confirmation.
    confirm(null, next);
  }

  return (
    <fieldset>
      <legend>{t("title")}</legend>
      <p>
        {status === "loading" && t("loading")}
        {status === "suggested" && t("suggested")}
        {status === "none" && t("noneSuggested")}
        {status === "unavailable" && t("unavailable")}
      </p>
      {ALLERGENS.map((allergen) => (
        <label key={allergen} style={{ marginRight: "0.75rem" }}>
          <input
            type="checkbox"
            checked={selected.includes(allergen)}
            onChange={() => toggle(allergen)}
          />
          {tAllergen(allergen)}
        </label>
      ))}
      <div>
        <button
          type="button"
          disabled={selected.length === 0}
          onClick={() => confirm("list", selected)}
        >
          {t("confirm")}
        </button>
        <button
          type="button"
          onClick={() => {
            setSelected([]);
            confirm("none", []);
          }}
        >
          {t("containsNone")}
        </button>
      </div>
      <p role="status">
        {confirmed === "list" &&
          t("confirmedList", { list: selected.map((item) => tAllergen(item)).join(", ") })}
        {confirmed === "none" && t("confirmedNone")}
        {confirmed === null && t("unconfirmed")}
      </p>
    </fieldset>
  );
}
