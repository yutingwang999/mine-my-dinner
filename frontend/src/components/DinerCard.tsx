"use client";

import { useLocale, useTranslations } from "next-intl";
import { useEffect, useState } from "react";

import { AddRule, RuleText, type ShownRule } from "@/components/RuleFields";
import { api, ApiError, entryName, type IngredientEntry, type Rule, type RuleWrite } from "@/lib/api";
import type { DinerAnswer, DinersPending, ProposedRule, RuleChoice } from "@/lib/chat";
import { useErrorText } from "@/lib/errors";

/**
 * A copy of backend `constants.DIET_WORDS` (spec 5.4); change them together. The backend
 * refuses these words twice: `api/diners.py` (a rule whose text contains one, code
 * `diet_word`) and `agent/diners.py` (`RuleChoice.no_diet_word` and `_check_rule`, a card
 * rule that is one). Here it only refuses a guest's text rule before the answer is sent.
 */
const DIET_WORDS = ["吃素", "素食", "全素", "vegetarian", "vegan"];

/** The backend's DinerAnswer limits: at most 20 guests and 50 unmatched people. */
const MAX_GUESTS = 20;
const MAX_OTHERS = 50;

type Choice = { rule: ProposedRule; ticked: boolean; save: boolean };

type Guest = { key: number; name: string; choices: Choice[]; dietWords: string[]; save: boolean };

type OthersAnswer = "none" | "unsure";

/** The card's edits, kept by the chat page so a failed confirm shows them again. */
export type DinerDraft = {
  selected: number[];
  stated: Record<number, Choice[]>;
  guests: Guest[];
  nextKey: number;
  othersAnswer: OthersAnswer | null;
};

type Props = {
  pending: DinersPending;
  busy: boolean;
  onConfirm: (answer: DinerAnswer) => void;
  /** Edits to start from instead of the interrupt's values. */
  draft?: DinerDraft;
  onDraft?: (draft: DinerDraft) => void;
};

function shown(rule: ProposedRule): ShownRule {
  return { ...rule, ingredient_name_zh: rule.ingredient_name, ingredient_name_en: rule.ingredient_name };
}

function toChoice({ rule_type, allergen, ingredient_id, text }: ProposedRule, save: boolean): RuleChoice {
  return { rule_type, allergen, ingredient_id, text, save };
}

function ticked(rules: ProposedRule[]): Choice[] {
  return rules.map((rule) => ({ rule, ticked: true, save: false }));
}

function hasDietWord(text: string | null): boolean {
  const folded = (text ?? "").trim().toLowerCase();
  return !!folded && DIET_WORDS.some((word) => folded.includes(word.toLowerCase()));
}

/** "vegetarian" shown as a word with the fixed hint, never as a rule (spec 5.4). */
function DietHint({ words }: { words: string[] }) {
  const t = useTranslations("chat.diners");
  if (words.length === 0) return null;
  return (
    <p className="notice notice-info diner-diet">
      {words.map((word) => (
        <span key={word} className="badge">
          {word}
        </span>
      ))}
      {t("dietHint")}
    </p>
  );
}

/** The proposed rules of one person: ticked by default; `withSave` adds "save to profile". */
function Choices({
  choices,
  withSave,
  disabled,
  onChange,
}: {
  choices: Choice[];
  withSave: boolean;
  disabled: boolean;
  onChange: (index: number, choice: Choice) => void;
}) {
  const t = useTranslations("chat.diners");
  if (choices.length === 0) return null;
  return (
    <ul className="diner-items">
      {choices.map((choice, index) => (
        <li key={index}>
          <label className="mini-field inline">
            <input
              type="checkbox"
              checked={choice.ticked}
              disabled={disabled}
              onChange={() => onChange(index, { ...choice, ticked: !choice.ticked })}
            />
            <span>
              <RuleText rule={shown(choice.rule)} wide />
            </span>
            {choice.rule.by_wording && (
              <span className="badge" title={t("byWordingHint")}>
                {t("byWording")}
              </span>
            )}
          </label>
          {withSave && (
            <label className="mini-field inline">
              <input
                type="checkbox"
                checked={choice.ticked && choice.save}
                disabled={disabled || !choice.ticked}
                onChange={() => onChange(index, { ...choice, save: !choice.save })}
              />
              {t("saveToProfile")}
            </label>
          )}
        </li>
      ))}
    </ul>
  );
}

/**
 * The diner card (spec 5.2): who eats this meal, their rules, the rules stated in the chat,
 * the people from the conversation who are not saved friends, and the unmatched rest.
 * Saved rules of the user and friends are edited through the rule API at once; everything
 * else is sent with the answer.
 */
export function DinerCard({ pending, busy, onConfirm, draft, onDraft }: Props) {
  const t = useTranslations("chat.diners");
  const tCommon = useTranslations("common");
  const errorText = useErrorText();
  const locale = useLocale();
  const [selected, setSelected] = useState<Set<number>>(
    () =>
      new Set(draft?.selected ?? pending.rows.filter((row) => row.selected).map((row) => row.diner_id)),
  );
  // The interrupt's saved rules show first; the effect below replaces them with the current ones.
  const [rules, setRules] = useState<Record<number, Rule[]>>(() =>
    Object.fromEntries(pending.rows.map((row) => [row.diner_id, row.rules])),
  );
  const [stated, setStated] = useState<Record<number, Choice[]>>(
    () => draft?.stated ?? Object.fromEntries(pending.rows.map((row) => [row.diner_id, ticked(row.proposed)])),
  );
  const [guests, setGuests] = useState<Guest[]>(
    () =>
      draft?.guests ??
      pending.guests.map((guest, key) => ({
        key,
        name: guest.name ?? "",
        choices: ticked(guest.proposed),
        dietWords: guest.diet_words,
        save: false,
      })),
  );
  const [nextKey, setNextKey] = useState(draft?.nextKey ?? pending.guests.length);
  // Row keys being edited: "diner:<id>" or "guest:<key>".
  const [editing, setEditing] = useState<Set<string>>(new Set());
  const [othersAnswer, setOthersAnswer] = useState<OthersAnswer | null>(draft?.othersAnswer ?? null);
  const [error, setError] = useState<unknown>(null);

  // Saved rules may have changed since the interrupt (edited on this card before a reload or
  // a failed confirm, or on the people page), so read them again; a failed read keeps them.
  useEffect(() => {
    let active = true;
    for (const row of pending.rows) {
      api
        .listRules(row.diner_id)
        .then((current) => {
          if (active) setRules((old) => ({ ...old, [row.diner_id]: current }));
        })
        .catch(() => {});
    }
    return () => {
      active = false;
    };
  }, [pending]);

  useEffect(() => {
    onDraft?.({ selected: [...selected], stated, guests, nextKey, othersAnswer });
  }, [onDraft, selected, stated, guests, nextKey, othersAnswer]);

  const eating = selected.size + guests.length;
  const headcount = Math.max(pending.headcount, eating);
  // The backend accepts at most 50 unmatched people; a larger stated headcount is cut there.
  const others = Math.min(MAX_OTHERS, headcount - eating);
  const roomForGuests = MAX_GUESTS - guests.length;

  function toggleEditing(key: string) {
    setEditing((current) => {
      const next = new Set(current);
      if (!next.delete(key)) next.add(key);
      return next;
    });
  }

  function toggleSelected(dinerId: number) {
    setSelected((current) => {
      const next = new Set(current);
      if (!next.delete(dinerId)) next.add(dinerId);
      return next;
    });
  }

  function newGuests(count: number): Guest[] {
    return Array.from({ length: count }, (_, index) => ({
      key: nextKey + index,
      name: "",
      choices: [],
      dietWords: [],
      save: false,
    }));
  }

  function addGuests(count: number) {
    setGuests((current) => [...current, ...newGuests(count)]);
    setNextKey((current) => current + count);
    setOthersAnswer(null);
  }

  function updateGuest(key: number, change: (guest: Guest) => Guest) {
    setGuests((current) => current.map((guest) => (guest.key === key ? change(guest) : guest)));
  }

  async function removeRule(dinerId: number, ruleId: number) {
    setError(null);
    try {
      await api.deleteRule(ruleId);
      setRules((current) => ({ ...current, [dinerId]: current[dinerId].filter((rule) => rule.id !== ruleId) }));
    } catch (caught) {
      setError(caught);
    }
  }

  async function addProfileRule(dinerId: number, write: RuleWrite) {
    const rule = await api.createRule(dinerId, write);
    setRules((current) => ({ ...current, [dinerId]: [...current[dinerId], rule] }));
  }

  /** A guest's rule applies to this meal only (or goes to the new friend when saved). */
  async function addGuestRule(key: number, write: RuleWrite, entry: IngredientEntry | null) {
    if (hasDietWord(write.text)) throw new ApiError(422, "diet_word");
    if (write.ingredient_id === null && write.allergen === null && !write.text?.trim()) {
      throw new ApiError(422, "empty_rule");
    }
    const rule: ProposedRule = {
      ...write,
      text: write.text?.trim() || null,
      ingredient_name: entry ? entryName(entry, locale) : null,
      by_wording: !!write.text,
    };
    updateGuest(key, (guest) => ({ ...guest, choices: [...guest.choices, { rule, ticked: true, save: false }] }));
  }

  function answer(): DinerAnswer {
    const ids = pending.rows.map((row) => row.diner_id).filter((id) => selected.has(id));
    const chosen: Record<number, RuleChoice[]> = {};
    for (const id of ids) {
      const rulesOf = (stated[id] ?? []).filter((choice) => choice.ticked);
      if (rulesOf.length) chosen[id] = rulesOf.map((choice) => toChoice(choice.rule, choice.save));
    }
    return {
      selected: ids,
      stated: chosen,
      guests: guests.map((guest) => {
        const name = guest.name.trim() || null;
        return {
          name,
          rules: guest.choices.filter((choice) => choice.ticked).map((choice) => toChoice(choice.rule, false)),
          save: guest.save && name !== null,
        };
      }),
      others,
      others_answer: others > 0 ? othersAnswer : null,
    };
  }

  return (
    <section className="card diner-card">
      <p>
        <strong>{t("title", { count: headcount })}</strong>
      </p>
      {headcount > pending.headcount && <p className="hint">{t("headcountChanged", { count: headcount })}</p>}
      {pending.nudge && <p className="notice notice-warning">{t("nudge")}</p>}
      <ul className="diner-list">
        {pending.rows.map((row) => {
          const name = row.is_self ? t("me") : (row.name ?? "");
          const editKey = `diner:${row.diner_id}`;
          const isEditing = editing.has(editKey);
          const saved = rules[row.diner_id] ?? [];
          const isSelected = selected.has(row.diner_id);
          return (
            <li key={row.diner_id} data-testid={`diner-row-${row.diner_id}`} className="diner">
              <div className="diner-line">
                <label className="diner-who">
                  <input
                    type="checkbox"
                    checked={isSelected}
                    disabled={busy}
                    onChange={() => toggleSelected(row.diner_id)}
                  />
                  <strong>{name}</strong>
                </label>
                <div className="diner-info">
                  {saved.length === 0 ? (
                    <p className="hint">{t("noRules")}</p>
                  ) : (
                    <ul className="diner-items">
                      {saved.map((rule) => (
                        <li key={rule.id}>
                          <span>
                            <RuleText rule={rule} wide />
                          </span>
                          {isEditing && (
                            <button
                              type="button"
                              className="btn-danger btn-small"
                              disabled={busy}
                              onClick={() => removeRule(row.diner_id, rule.id)}
                            >
                              {tCommon("remove")}
                            </button>
                          )}
                        </li>
                      ))}
                    </ul>
                  )}
                  <Choices
                    choices={stated[row.diner_id] ?? []}
                    withSave
                    disabled={busy || !isSelected}
                    onChange={(index, choice) =>
                      setStated((current) => ({
                        ...current,
                        [row.diner_id]: current[row.diner_id].map((old, at) => (at === index ? choice : old)),
                      }))
                    }
                  />
                  <DietHint words={row.diet_words} />
                </div>
                <button type="button" className="btn-text" disabled={busy} onClick={() => toggleEditing(editKey)}>
                  {isEditing ? t("done") : t("edit")}
                </button>
              </div>
              {isEditing && (
                <div className="diner-edit">
                  <p className="hint">{t("profileNote")}</p>
                  <AddRule onAdd={(write) => addProfileRule(row.diner_id, write)} />
                </div>
              )}
            </li>
          );
        })}
      </ul>
      {guests.length > 0 && <p className="diner-section">{t("guests")}</p>}
      {guests.length > 0 && (
        <ul className="diner-list">
          {guests.map((guest, index) => {
            const editKey = `guest:${guest.key}`;
            const named = guest.name.trim() !== "";
            return (
              <li key={guest.key} data-testid={`guest-row-${index}`} className="diner">
                <div className="diner-line">
                  <div className="diner-who">
                    <span className="diner-spacer" aria-hidden="true" />
                    <input
                      className="diner-guest-name"
                      aria-label={t("guestName")}
                      value={guest.name}
                      placeholder={t("guestName")}
                      maxLength={40}
                      disabled={busy}
                      onChange={(event) => updateGuest(guest.key, (old) => ({ ...old, name: event.target.value }))}
                    />
                  </div>
                  <div className="diner-info">
                    <Choices
                      choices={guest.choices}
                      withSave={false}
                      disabled={busy}
                      onChange={(at, choice) =>
                        updateGuest(guest.key, (old) => ({
                          ...old,
                          choices: old.choices.map((item, position) => (position === at ? choice : item)),
                        }))
                      }
                    />
                    {guest.choices.length === 0 && guest.dietWords.length === 0 && (
                      <span className="hint">{t("noRules")}</span>
                    )}
                    <DietHint words={guest.dietWords} />
                    <label className="diner-save">
                      <input
                        type="checkbox"
                        checked={guest.save && named}
                        disabled={busy || !named}
                        onChange={() => updateGuest(guest.key, (old) => ({ ...old, save: !old.save }))}
                      />
                      {t("saveFriend")}
                    </label>
                  </div>
                  <div className="diner-ends">
                    <button type="button" className="btn-text" disabled={busy} onClick={() => toggleEditing(editKey)}>
                      {editing.has(editKey) ? t("done") : t("edit")}
                    </button>
                    <button
                      type="button"
                      className="btn-text btn-danger"
                      disabled={busy}
                      onClick={() => setGuests((current) => current.filter((old) => old.key !== guest.key))}
                    >
                      {tCommon("remove")}
                    </button>
                  </div>
                </div>
                {editing.has(editKey) && (
                  <div className="diner-edit">
                    <AddRule onAdd={(write, entry) => addGuestRule(guest.key, write, entry)} />
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {others > 0 && (
        <div className="diner-others">
          <p>{t("unmatched", { count: others })}</p>
          <div className="actions">
            <button
              type="button"
              aria-pressed={othersAnswer === "none"}
              disabled={busy}
              onClick={() => setOthersAnswer("none")}
            >
              {t("noLimits")}
            </button>
            <button type="button" disabled={busy || others > roomForGuests} onClick={() => addGuests(others)}>
              {t("fillIn")}
            </button>
            <button
              type="button"
              aria-pressed={othersAnswer === "unsure"}
              disabled={busy}
              onClick={() => setOthersAnswer("unsure")}
            >
              {t("unsure")}
            </button>
          </div>
        </div>
      )}
      {error !== null && (
        <p role="alert" className="notice notice-warning">
          {errorText(error)}
        </p>
      )}
      <div className="diner-footer">
        <button type="button" className="btn-text" disabled={busy || roomForGuests < 1} onClick={() => addGuests(1)}>
          <span aria-hidden="true">+ </span>
          {t("addGuest")}
        </button>
        <button type="button" className="btn-primary" disabled={busy} onClick={() => onConfirm(answer())}>
          {t("confirm")}
        </button>
      </div>
    </section>
  );
}
