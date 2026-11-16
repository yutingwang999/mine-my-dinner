"use client";

import { useTranslations } from "next-intl";
import { useEffect, useRef, useState } from "react";

import { type Allergen, ALLERGENS, api, type EntrySuggestion } from "@/lib/api";

type Status = "loading" | "suggested" | "none" | "unavailable";

type Suggestion = EntrySuggestion;

// Requests still waiting, so the same name asked twice at once costs one model call.
// Development mode runs every effect twice, which would otherwise double each request.
const pending = new Map<string, Promise<Suggestion>>();

function suggestOnce(name: string): Promise<Suggestion> {
  let request = pending.get(name);
  if (request === undefined) {
    request = api.suggestEntry(name).finally(() => pending.delete(name));
    pending.set(name, request);
  }
  return request;
}

type Confirmed = "list" | "none" | null;

type Props = {
  name: string;
  /** Null until the user confirms, which means the allergens stay unknown. */
  onChange: (allergens: Allergen[] | null) => void;
  /** Called once when the model answers, so a form can prefill what else it suggests. */
  onSuggestion?: (suggestion: EntrySuggestion) => void;
};

/**
 * Shows a model's allergen suggestion, preselected but without effect until the
 * user confirms it. An empty suggestion is never treated as "contains none".
 */
export function AllergenPicker({ name, onChange, onSuggestion }: Props) {
  const t = useTranslations("allergens");
  const tAllergen = useTranslations("allergen");
  const [status, setStatus] = useState<Status>("loading");
  const [selected, setSelected] = useState<Allergen[]>([]);
  const [confirmed, setConfirmed] = useState<Confirmed>(null);
  const suggestionHandler = useRef(onSuggestion);
  useEffect(() => {
    suggestionHandler.current = onSuggestion;
  });

  useEffect(() => {
    let active = true;
    suggestOnce(name)
      .then((suggestion) => {
        const { suggested } = suggestion;
        if (!active) return;
        // A reply without these parts counts as no suggestion for them.
        suggestionHandler.current?.({
          ...suggestion,
          category: suggestion.category ?? null,
          stock_mode: suggestion.stock_mode ?? null,
        });
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
    <fieldset className="allergen-picker">
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
