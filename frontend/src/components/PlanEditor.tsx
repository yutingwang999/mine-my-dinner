"use client";

import { useLocale, useTranslations } from "next-intl";
import { type FormEvent, type Ref, useEffect, useId, useRef, useState } from "react";

import { ResolveUnknownName } from "@/components/AllergenResolvePanels";
import { ApiError } from "@/lib/api";
import type { ChatPlan, Locale } from "@/lib/chat";
import { useErrorText } from "@/lib/errors";
import {
  addDish,
  type Conflict,
  keepDish,
  MEAL_KEY,
  removeDish,
  restorePlan,
  type SearchDislike,
  searchPlan,
  type SearchResult,
} from "@/lib/planEdits";

/** Who a conflict or dislike is about: the user as "you", a guest without a name as "a guest". */
type Person = { diner_name: string | null; is_self: boolean };

/** The sentences for conflicts (plan 3a spec 5.2), written from the conflict's fields. */
export function useConflictText() {
  const t = useTranslations("chat.edit");
  const tAllergen = useTranslations("allergen");
  const who = (person: Person) => (person.is_self ? t("self") : (person.diner_name ?? t("guest")));
  const rule = (person: Person, ruleType: string | null, target: string) =>
    t("rule", {
      type: ruleType === "allergy" ? "allergy" : ruleType === "dislike" ? "dislike" : "restriction",
      self: person.is_self ? "yes" : "no",
      target,
    });
  const target = (conflict: Conflict) =>
    conflict.kind === "allergen" && conflict.allergen
      ? tAllergen(conflict.allergen)
      : conflict.kind === "ingredient"
        ? (conflict.ingredient_name ?? "")
        : (conflict.text ?? "");
  const lines = (conflict: Conflict) => conflict.lines.join(t("lineSeparator"));
  // This meal's own wish ("今天不想吃肉", plan 3b-1b spec 5.4) is never shown as a person's rule.
  const mealWish = (conflict: Conflict) =>
    conflict.diner_key === MEAL_KEY ? t("mealWish", { name: target(conflict) }) : null;
  return {
    /** "小王：忌口 豆腐", "这顿不要肉", or "花椒油：过敏原未知" for unknown allergens. */
    line: (conflict: Conflict) =>
      conflict.kind === "unknown_allergens"
        ? t("unknownLine", { lines: lines(conflict) })
        : (mealWish(conflict) ??
          t("line", { who: who(conflict), rule: rule(conflict, conflict.rule_type, target(conflict)) })),
    dislike: (dislike: SearchDislike) =>
      t("line", { who: who(dislike), rule: rule(dislike, "dislike", dislike.line) }),
    /** A conflict the user confirmed: "已确认保留：小王 忌口 牛肉". */
    kept: (conflict: Conflict) => {
      if (conflict.kind === "unknown_allergens") return t("keptUnknown", { lines: lines(conflict) });
      if (conflict.diner_key === MEAL_KEY) return t("keptMealWish", { name: target(conflict) });
      return t("kept", { who: who(conflict), rule: rule(conflict, conflict.rule_type, target(conflict)) });
    },
    /** Why a dish already in the plan is marked red: what changed since planning. */
    reason: (conflict: Conflict) => {
      if (conflict.kind === "unknown_allergens") return t("reasonUnknown", { lines: lines(conflict) });
      const wish = mealWish(conflict);
      if (wish !== null) return t("reasonMealWish", { wish, lines: lines(conflict) || target(conflict) });
      return t("reason", {
        who: who(conflict),
        rule: rule(conflict, conflict.rule_type, target(conflict)),
        lines: lines(conflict) || target(conflict),
      });
    },
  };
}

/** An add or keep the backend refused until its conflicts are confirmed. */
export type Asking = { action: "add" | "keep"; recipeId: number; conflicts: Conflict[] };

/**
 * The card's edit calls. Each returns true when the edit applied (the new card is passed to
 * `onEdited`); a 409 `needs_confirmation` opens `asking` instead of an error, and a 409
 * `thread_busy` also calls `onRefused`, so the page can disable editing. `onEditing` hears
 * when a request starts and ends, so the page blocks sending a message meanwhile.
 */
export function usePlanEdit({
  threadId,
  onEdited,
  onRefused,
  onEditing,
}: {
  threadId: string;
  onEdited: (plan: ChatPlan) => void;
  onRefused?: () => void;
  onEditing?: (saving: boolean) => void;
}) {
  const locale = useLocale() as Locale;
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [asking, setAsking] = useState<Asking | null>(null);

  function fail(caught: unknown) {
    setError(caught);
    if (caught instanceof ApiError && caught.code === "thread_busy") onRefused?.();
  }

  async function run(call: () => Promise<ChatPlan>, ask?: Omit<Asking, "conflicts">): Promise<boolean> {
    setSaving(true);
    onEditing?.(true);
    setError(null);
    try {
      const plan = await call();
      setAsking(null);
      onEdited(plan);
      return true;
    } catch (caught) {
      if (ask && caught instanceof ApiError && caught.code === "needs_confirmation") {
        const conflicts = Array.isArray(caught.body.conflicts) ? (caught.body.conflicts as Conflict[]) : [];
        setAsking({ ...ask, conflicts });
      } else {
        setAsking(null);
        fail(caught);
      }
      return false;
    } finally {
      setSaving(false);
      onEditing?.(false);
    }
  }

  const add = (recipeId: number, confirm: Conflict[] = []) =>
    run(() => addDish(threadId, recipeId, confirm, locale), { action: "add", recipeId });
  const keep = (recipeId: number, confirm: Conflict[]) =>
    run(() => keepDish(threadId, recipeId, confirm, locale), { action: "keep", recipeId });

  return {
    saving,
    error,
    asking,
    /** Tells the page the thread refused an edit as busy. */
    refused: () => onRefused?.(),
    remove: (recipeId: number) => run(() => removeDish(threadId, recipeId, locale)),
    add,
    keep,
    restore: () => run(() => restorePlan(threadId, locale)),
    /** Sends the asked action again with every conflict it named confirmed. */
    confirm: () =>
      asking === null
        ? Promise.resolve(false)
        : asking.action === "add"
          ? add(asking.recipeId, asking.conflicts)
          : keep(asking.recipeId, asking.conflicts),
    cancel: () => setAsking(null),
  };
}

export type PlanEdit = ReturnType<typeof usePlanEdit>;

/**
 * The confirmation for a conflicting dish: each diner and rule, or the ingredient of unknown
 * allergens. When adding, an unknown-allergen ingredient can be confirmed on the spot, and the
 * add is asked again (`onRecheck`).
 */
export function ConfirmConflicts({
  asking,
  disabled,
  onConfirm,
  onCancel,
  onRecheck,
}: {
  asking: Asking;
  disabled: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  onRecheck?: () => void;
}) {
  const t = useTranslations("chat.edit");
  const text = useConflictText();
  const titleId = useId();
  const first = useRef<HTMLButtonElement | null>(null);
  // An inline panel, not a modal: focus moves to its first button when it opens.
  useEffect(() => first.current?.focus(), []);
  const unknownLines = [
    ...new Set(asking.conflicts.filter((c) => c.kind === "unknown_allergens").flatMap((c) => c.lines)),
  ];
  return (
    <div role="group" aria-labelledby={titleId} className="plan-confirm">
      <h3 id={titleId}>{t("confirmTitle")}</h3>
      <ul>
        {asking.conflicts.map((conflict, index) => (
          <li key={index}>{text.line(conflict)}</li>
        ))}
      </ul>
      {onRecheck && unknownLines.length > 0 && (
        <>
          <p className="hint">{t("confirmUnknownHint")}</p>
          <ul className="left-out-list">
            {unknownLines.map((name) => (
              <li key={name}>
                <span>{name}</span>
                <ResolveUnknownName name={name} onResolved={onRecheck} />
              </li>
            ))}
          </ul>
        </>
      )}
      <div className="actions">
        <button type="button" ref={first} disabled={disabled} onClick={onCancel}>
          {t("cancel")}
        </button>
        <button type="button" className="btn-primary" disabled={disabled} onClick={onConfirm}>
          {asking.action === "add" ? t("confirmAdd") : t("confirmKeep")}
        </button>
      </div>
    </div>
  );
}

/** How many search results show before "Show more". */
const FIRST_RESULTS = 5;

/** "+ Add a dish": a search of the library inside the card, each result with its markers. */
export function AddDish({
  threadId,
  edit,
  disabled,
  planIds,
  buttonRef,
}: {
  threadId: string;
  edit: PlanEdit;
  disabled: boolean;
  /** The dishes now in the plan: a result's "already in the plan" mark follows them. */
  planIds: number[];
  /** The "+ Add a dish" button, which gets focus after a dish is removed. */
  buttonRef?: Ref<HTMLButtonElement>;
}) {
  const t = useTranslations("chat.edit");
  const tPlan = useTranslations("chat.plan");
  const tDish = useTranslations("dishType");
  const locale = useLocale() as Locale;
  const errorText = useErrorText();
  const text = useConflictText();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  // The result with the text it was searched for, which the empty message names.
  const [result, setResult] = useState<(SearchResult & { query: string }) | null>(null);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<unknown>(null);
  // The first results only, so the card stays short; "Show more" reveals the rest.
  const [showAll, setShowAll] = useState(false);
  const input = useRef<HTMLInputElement | null>(null);
  const row = useRef<HTMLDivElement | null>(null);
  // Closing puts focus back on "+ Add a dish", which takes the search's place again.
  const refocus = useRef(false);
  // Typing goes to the search box, not the chat composer, as soon as the search opens.
  useEffect(() => {
    if (open) input.current?.focus();
    else if (refocus.current) {
      refocus.current = false;
      row.current?.querySelector<HTMLButtonElement>(".plan-add-open")?.focus();
    }
  }, [open]);
  const busy = disabled || edit.saving;

  function close() {
    refocus.current = true;
    setOpen(false);
    setResult(null);
    setQuery("");
    setError(null);
    edit.cancel();
  }

  async function search(event: FormEvent) {
    event.preventDefault();
    const q = query.trim();
    if (!q) return;
    setSearching(true);
    setError(null);
    try {
      setResult({ ...(await searchPlan(threadId, q, locale)), query: q });
      setShowAll(false);
    } catch (caught) {
      setError(caught);
      if (caught instanceof ApiError && caught.code === "thread_busy") edit.refused();
    } finally {
      setSearching(false);
    }
  }

  // An applied add closes the search: its "already in the plan" marks are out of date.
  const done = (applied: boolean) => {
    if (applied) close();
  };

  return (
    // The last row of the dish list: "+ Add a dish", which turns into the search in place.
    <div className="plan-add" ref={row}>
      {!open && (
        <button type="button" ref={buttonRef} className="plan-add-open" disabled={busy} onClick={() => setOpen(true)}>
          {t("addDish")}
        </button>
      )}
      {open && (
        <div
          className="plan-search"
          onKeyDown={(event) => {
            if (event.key === "Escape" && !event.nativeEvent.isComposing) close();
          }}
        >
          <form role="search" className="plan-search-form" onSubmit={search}>
            <input
              ref={input}
              type="search"
              aria-label={t("searchLabel")}
              placeholder={t("searchLabel")}
              value={query}
              maxLength={200}
              onChange={(event) => setQuery(event.target.value)}
            />
            <button type="submit" disabled={busy || searching}>
              {t("search")}
            </button>
            <button type="button" className="plan-search-close" aria-label={t("closeSearch")} onClick={close}>
              <span aria-hidden="true">×</span>
            </button>
          </form>
          {searching && <p className="hint">{t("searching")}</p>}
          {error !== null && (
            <p role="alert" className="notice notice-warning">
              {errorText(error)}
            </p>
          )}
          {result !== null && result.hits.length === 0 && <p className="hint">{t("noResults", { query: result.query })}</p>}
          {result !== null && result.hits.length > 0 && (
            <ul aria-label={t("results")} className="plan-search-results">
              {(showAll ? result.hits : result.hits.slice(0, FIRST_RESULTS)).map((hit) => (
                <li key={hit.id} className="plan-search-hit">
                  <div className="plan-search-main">
                    <strong>{hit.title}</strong>
                    <span className="hint">
                      {tDish(hit.dish_type)} ·{" "}
                      {hit.time_min === null ? tPlan("timeUnknown") : t("time", { minutes: hit.time_min })}
                    </span>
                    {hit.missing.length > 0 && (
                      <span className="hint">{t("missing", { list: hit.missing.join(t("lineSeparator")) })}</span>
                    )}
                    {hit.missing_cookware.length > 0 && (
                      <span className="hint">
                        {t("missingCookware", { list: hit.missing_cookware.join(t("lineSeparator")) })}
                      </span>
                    )}
                    {hit.conflicts.map((conflict, index) => (
                      <span key={`c${index}`} className="plan-search-conflict">
                        <span aria-hidden="true">⚠ </span>
                        <span>{text.line(conflict)}</span>
                      </span>
                    ))}
                    {hit.dislikes.map((dislike, index) => (
                      <span key={`d${index}`} className="hint">
                        {text.dislike(dislike)}
                      </span>
                    ))}
                  </div>
                  {planIds.includes(hit.id) ? (
                    <span className="hint plan-search-in">{t("inPlan")}</span>
                  ) : (
                    <button
                      type="button"
                      className="plan-search-add"
                      disabled={busy || edit.asking !== null}
                      onClick={() => void edit.add(hit.id).then(done)}
                    >
                      {t("add")}
                    </button>
                  )}
                  {edit.asking?.action === "add" && edit.asking.recipeId === hit.id && (
                    <ConfirmConflicts
                      asking={edit.asking}
                      disabled={busy}
                      onConfirm={() => void edit.confirm().then(done)}
                      onCancel={edit.cancel}
                      onRecheck={() => void edit.add(hit.id).then(done)}
                    />
                  )}
                </li>
              ))}
            </ul>
          )}
          {result !== null && !showAll && result.hits.length > FIRST_RESULTS && (
            <button type="button" className="btn-text" onClick={() => setShowAll(true)}>
              {t("showMore")}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
