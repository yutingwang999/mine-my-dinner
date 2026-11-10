"use client";

import { useTranslations } from "next-intl";
import { type FormEvent, useState } from "react";

import { Link, useRouter } from "@/i18n/navigation";
import { api, ApiError, type BatchItem, type RecipeDraft } from "@/lib/api";
import { useDrafts } from "@/lib/drafts";
import { useErrorText } from "@/lib/errors";

type Source = "text" | "url" | "screenshots" | "batch";

const LINK = /https?:\/\/\S+/g;

function linkCount(text: string): number {
  return text.match(LINK)?.length ?? 0;
}

/** Parts between lines of only "---", as the batch file import splits them. */
function recipeParts(text: string): number {
  const parts: string[] = [""];
  for (const line of text.split(/\r?\n/)) {
    if (line.trim() === "---") {
      parts.push("");
    } else {
      parts[parts.length - 1] += line;
    }
  }
  return parts.filter((part) => part.trim()).length;
}

/** Stops input that would import the wrong thing, before any paid call. */
function checkPaste(text: string): void {
  // A stray separator before or after one recipe is fine; two recipes are not.
  if (recipeParts(text) > 1) {
    throw new ApiError(0, "several_recipes");
  }
  if (linkCount(text) > 1 && text.replace(LINK, "").trim() === "") {
    throw new ApiError(0, "several_links");
  }
}

function checkLink(url: string): void {
  if (linkCount(url) > 1) {
    throw new ApiError(0, "several_links");
  }
}

/** Every input keeps its value after a failure, so nothing has to be retyped. */
export function ImportForm() {
  const t = useTranslations("import");
  const errorText = useErrorText();
  const router = useRouter();
  const { addDrafts } = useDrafts();
  const [text, setText] = useState("");
  const [url, setUrl] = useState("");
  const [images, setImages] = useState<File[]>([]);
  const [batchFile, setBatchFile] = useState<File | null>(null);
  const [busy, setBusy] = useState<Source | null>(null);
  const [error, setError] = useState<{ source: Source; error: unknown } | null>(null);
  const [batchItems, setBatchItems] = useState<BatchItem[] | null>(null);

  async function run(source: Source, action: () => Promise<void>) {
    setBusy(source);
    setError(null);
    try {
      await action();
    } catch (caught) {
      setError({ source, error: caught });
    } finally {
      setBusy(null);
    }
  }

  function open(draft: RecipeDraft) {
    addDrafts([draft]);
    router.push("/review");
  }

  function submit(source: Source, action: () => Promise<void>) {
    return (event: FormEvent) => {
      event.preventDefault();
      void run(source, action);
    };
  }

  function errorFor(source: Source) {
    if (error?.source !== source) return null;
    const suggestPaste = error.error instanceof ApiError && error.error.suggestPaste;
    return (
      <div role="alert" className="notice notice-warning">
        <p>{errorText(error.error)}</p>
        {suggestPaste && <p>{t("pasteSuggestion")}</p>}
      </div>
    );
  }

  const working = busy !== null;
  const queued = batchItems?.filter((item) => item.draft !== null).length ?? 0;

  return (
    <div className="import-grid">
      <div className="notice notice-info wide">
        <p>{t("rightsNotice")}</p>
        <p>{t("modelNotice")}</p>
      </div>

      <form
        className="card wide"
        onSubmit={submit("text", async () => {
          checkPaste(text);
          open(await api.importText(text));
        })}
      >
        <label className="stack-field">
          {t("textLabel")}
          <textarea rows={8} value={text} onChange={(event) => setText(event.target.value)} />
        </label>
        <p className="hint">{t("textHint")}</p>
        <div className="actions">
          <button type="submit" disabled={working}>
            {busy === "text" ? t("working") : t("textSubmit")}
          </button>
        </div>
        {errorFor("text")}
      </form>

      <form
        className="card"
        onSubmit={submit("url", async () => {
          checkLink(url);
          open(await api.importUrl(url));
        })}
      >
        <label className="stack-field">
          {t("urlLabel")}
          <input
            type="text"
            inputMode="url"
            placeholder="https://"
            value={url}
            onChange={(event) => setUrl(event.target.value)}
          />
        </label>
        <p className="hint">{t("urlHint")}</p>
        <div className="actions">
          <button type="submit" disabled={working}>
            {busy === "url" ? t("working") : t("urlSubmit")}
          </button>
        </div>
        {errorFor("url")}
      </form>

      <form
        className="card"
        onSubmit={submit("screenshots", async () => open(await api.importScreenshots(images)))}
      >
        <label className="stack-field">
          {t("screenshotsLabel")}
          <input
            type="file"
            multiple
            accept="image/png,image/jpeg,image/webp"
            onChange={(event) => setImages(Array.from(event.target.files ?? []))}
          />
        </label>
        <p className="hint">{t("screenshotsHint")}</p>
        <div className="actions">
          <button type="submit" disabled={working || images.length === 0}>
            {busy === "screenshots" ? t("working") : t("screenshotsSubmit")}
          </button>
        </div>
        {errorFor("screenshots")}
      </form>

      <form
        className="card"
        onSubmit={submit("batch", async () => {
          if (batchFile === null) return;
          setBatchItems(null);
          const { items } = await api.importBatch(batchFile);
          addDrafts(items.flatMap((item) => (item.draft ? [item.draft] : [])));
          setBatchItems(items);
        })}
      >
        <label className="stack-field">
          {t("batchLabel")}
          <input
            type="file"
            accept=".txt,text/plain"
            onChange={(event) => setBatchFile(event.target.files?.[0] ?? null)}
          />
        </label>
        <p className="hint">{t("batchHint")}</p>
        <div className="actions">
          <button type="submit" disabled={working || batchFile === null}>
            {busy === "batch" ? t("working") : t("batchSubmit")}
          </button>
        </div>
        {errorFor("batch")}
        {batchItems !== null && (
          <div className="batch-result">
            <ul>
              {batchItems
                .filter((item) => item.error !== null)
                .map((item) => (
                  <li key={item.index}>
                    {t("batchItemFailed", {
                      number: item.index + 1,
                      error: errorText(new ApiError(0, item.error ?? "unknown")),
                    })}
                  </li>
                ))}
            </ul>
            <p>{t("batchQueued", { count: queued })}</p>
            {queued > 0 && <Link href="/review">{t("reviewDrafts")}</Link>}
          </div>
        )}
      </form>
    </div>
  );
}
