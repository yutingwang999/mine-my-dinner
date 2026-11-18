"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";

import { ConfirmDelete } from "@/components/ConfirmDelete";
import { PagedList } from "@/components/PagedList";
import { AddRule, useRuleText } from "@/components/RuleFields";
import { api, type Diner } from "@/lib/api";
import { useErrorText } from "@/lib/errors";
import { useLoad } from "@/lib/useLoad";

function DinerCard({ diner, onChanged }: { diner: Diner; onChanged: () => void }) {
  const t = useTranslations("people");
  const tCommon = useTranslations("common");
  const errorText = useErrorText();
  const ruleText = useRuleText(true);
  const [renaming, setRenaming] = useState(false);
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState(diner.display_name);
  const [error, setError] = useState<unknown>(null);
  const rules = useLoad(() => api.listRules(diner.id), [diner.id]);

  async function run(action: () => Promise<unknown>) {
    setError(null);
    try {
      await action();
      return true;
    } catch (caught) {
      setError(caught);
      return false;
    }
  }

  // The user's own record is always "me"; only friends have a name to change.
  const label = diner.is_self ? t("me") : diner.display_name;

  async function saveName() {
    if (await run(() => api.renameDiner(diner.id, name))) {
      setRenaming(false);
      onChanged();
    }
  }

  // One line: name | rules | "+ New rule", rename, delete. The self row keeps the last two
  // slots empty so "+ New rule" lines up on every row.
  return (
    <li data-testid={`diner-${diner.id}`} className="person">
      <div className="person-name">
        {renaming && !diner.is_self ? (
          <input
            aria-label={t("displayName")}
            value={name}
            onChange={(event) => setName(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && name.trim()) saveName();
            }}
          />
        ) : (
          <strong>{label}</strong>
        )}
      </div>
      <div className="person-rules">
        {rules.data?.length === 0 && <span className="hint">{t("noRules")}</span>}
        {rules.data?.map((rule) => {
          const text = ruleText(rule);
          return (
            <span key={rule.id} className="rule-chip">
              <span>{text}</span>
              <button
                type="button"
                className="rule-chip-remove"
                aria-label={`${tCommon("remove")} ${text}`}
                title={`${tCommon("remove")} ${text}`}
                onClick={async () => {
                  if (await run(() => api.deleteRule(rule.id))) rules.reload();
                }}
              >
                ×
              </button>
            </span>
          );
        })}
      </div>
      <div className="person-actions">
        <button
          type="button"
          className="btn-text person-add"
          aria-expanded={adding}
          onClick={() => setAdding((open) => !open)}
        >
          {adding ? (
            t("closeRule")
          ) : (
            <>
              <span aria-hidden="true">+ </span>
              {t("newRule")}
            </>
          )}
        </button>
        {diner.is_self ? (
          <span className="person-slot" aria-hidden="true" />
        ) : renaming ? (
          <button type="button" className="btn-text person-rename" disabled={!name.trim()} onClick={saveName}>
            {tCommon("save")}
          </button>
        ) : (
          <button type="button" className="btn-text person-rename" onClick={() => setRenaming(true)}>
            {t("rename")}
          </button>
        )}
        {diner.is_self ? (
          <span className="person-slot" aria-hidden="true" />
        ) : (
          <ConfirmDelete
            className="btn-text btn-danger person-delete"
            name={diner.display_name}
            notice={t("deleteFriendNotice")}
            onDelete={async () => {
              await api.deleteDiner(diner.id);
              onChanged();
            }}
          />
        )}
      </div>
      {diner.nickname_warning && !diner.is_self && (
        <p className="notice notice-info person-note">{t("nicknameWarning")}</p>
      )}
      {adding && (
        <div className="diner-edit person-edit">
          <AddRule
            onAdd={async (rule) => {
              await api.createRule(diner.id, rule);
              rules.reload();
            }}
          />
        </div>
      )}
      {error !== null && (
        <p role="alert" className="notice notice-warning person-note">
          {errorText(error)}
        </p>
      )}
    </li>
  );
}

export function PeopleManager() {
  const t = useTranslations("people");
  const errorText = useErrorText();
  const [name, setName] = useState("");
  const [error, setError] = useState<unknown>(null);
  const { data, reload } = useLoad(api.listDiners, []);

  async function addFriend() {
    setError(null);
    try {
      await api.createDiner(name);
      setName("");
      reload();
    } catch (caught) {
      setError(caught);
    }
  }

  return (
    <div>
      <section className="card">
        <PagedList items={data ?? []} list="diners" className="people-list" hideOnOnePage>
          {(visible) =>
            visible.map((diner) => <DinerCard key={diner.id} diner={diner} onChanged={reload} />)
          }
        </PagedList>
      </section>
      <section className="card">
        <h2>{t("addFriend")}</h2>
        <p className="hint">{t("friendHint")}</p>
        <div className="line-fields">
          <label className="mini-field">
            {t("displayName")}
            <input value={name} onChange={(event) => setName(event.target.value)} />
          </label>
          {/* A blank name is refused by the server with a generic error, so it cannot be sent. */}
          <button type="button" className="btn-primary" disabled={!name.trim()} onClick={addFriend}>
            {t("addFriend")}
          </button>
        </div>
        {error !== null && (
          <p role="alert" className="notice notice-warning">
            {errorText(error)}
          </p>
        )}
      </section>
    </div>
  );
}
