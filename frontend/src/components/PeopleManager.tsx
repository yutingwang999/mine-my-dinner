"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";

import { ConfirmDelete } from "@/components/ConfirmDelete";
import { PagedList } from "@/components/PagedList";
import { AddRule, RuleText } from "@/components/RuleFields";
import { api, type Diner } from "@/lib/api";
import { useErrorText } from "@/lib/errors";
import { useLoad } from "@/lib/useLoad";

function DinerCard({ diner, onChanged }: { diner: Diner; onChanged: () => void }) {
  const t = useTranslations("people");
  const tCommon = useTranslations("common");
  const errorText = useErrorText();
  const [renaming, setRenaming] = useState(false);
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

  return (
    <li data-testid={`diner-${diner.id}`} className="diner">
      <div className="actions">
        {renaming && !diner.is_self ? (
          <span className="actions">
            <input aria-label={t("displayName")} value={name} onChange={(event) => setName(event.target.value)} />
            <button
              type="button"
              className="btn-primary"
              onClick={async () => {
                if (await run(() => api.renameDiner(diner.id, name))) {
                  setRenaming(false);
                  onChanged();
                }
              }}
            >
              {tCommon("save")}
            </button>
          </span>
        ) : (
          <strong>{label}</strong>
        )}{" "}
        {!renaming && !diner.is_self && (
          <button type="button" onClick={() => setRenaming(true)}>
            {t("rename")}
          </button>
        )}
        {!diner.is_self && (
          <ConfirmDelete
            name={diner.display_name}
            notice={t("deleteFriendNotice")}
            onDelete={async () => {
              await api.deleteDiner(diner.id);
              onChanged();
            }}
          />
        )}
      </div>
      <h3>{t("rules")}</h3>
      {rules.data?.length === 0 && <p className="hint">{t("noRules")}</p>}
      <ul className="row-list">
        {rules.data?.map((rule) => (
          <li key={rule.id}>
            <span>
              <RuleText rule={rule} />
            </span>
            <button
              type="button"
              className="btn-danger"
              onClick={async () => {
                if (await run(() => api.deleteRule(rule.id))) rules.reload();
              }}
            >
              {tCommon("remove")}
            </button>
          </li>
        ))}
      </ul>
      <AddRule
        onAdd={async (rule) => {
          await api.createRule(diner.id, rule);
          rules.reload();
        }}
      />
      {error !== null && (
        <p role="alert" className="notice notice-warning">
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
        <PagedList items={data ?? []} list="diners" className="diner-list">
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
          <button type="button" className="btn-primary" onClick={addFriend}>
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
