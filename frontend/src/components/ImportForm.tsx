"use client";

import { useTranslations } from "next-intl";
import { type FormEvent, useState } from "react";

import { Link, useRouter } from "@/i18n/navigation";
import { api, ApiError, type BatchItem, type RecipeDraft } from "@/lib/api";
import { useDrafts } from "@/lib/drafts";
import { useErrorText } from "@/lib/errors";

type Source = "text" | "url" | "screenshots" | "batch";

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
      <div role="alert">
        <p>{errorText(error.error)}</p>
        {suggestPaste && <p>{t("pasteSuggestion")}</p>}
      </div>
    );
  }

  const working = busy !== null;
  const queued = batchItems?.filter((item) => item.draft !== null).length ?? 0;

  return (
    <div>
      <p>{t("modelNotice")}</p>

      <form onSubmit={submit("text", async () => open(await api.importText(text)))}>
        <label>
          {t("textLabel")}
          <textarea rows={8} value={text} onChange={(event) => setText(event.target.value)} />
        </label>
        <button type="submit" disabled={working}>
          {busy === "text" ? t("working") : t("textSubmit")}
        </button>
        {errorFor("text")}
      </form>

      <form onSubmit={submit("url", async () => open(await api.importUrl(url)))}>
        <label>
          {t("urlLabel")}
          <input type="text" inputMode="url" value={url} onChange={(event) => setUrl(event.target.value)} />
        </label>
        <button type="submit" disabled={working}>
          {busy === "url" ? t("working") : t("urlSubmit")}
        </button>
        {errorFor("url")}
      </form>

      <form onSubmit={submit("screenshots", async () => open(await api.importScreenshots(images)))}>
        <label>
          {t("screenshotsLabel")}
          <input
            type="file"
            multiple
            accept="image/png,image/jpeg,image/webp"
            onChange={(event) => setImages(Array.from(event.target.files ?? []))}
          />
        </label>
        <p>{t("screenshotsHint")}</p>
        <button type="submit" disabled={working || images.length === 0}>
          {busy === "screenshots" ? t("working") : t("screenshotsSubmit")}
        </button>
        {errorFor("screenshots")}
      </form>

      <form
        onSubmit={submit("batch", async () => {
          if (batchFile === null) return;
          setBatchItems(null);
          const { items } = await api.importBatch(batchFile);
          addDrafts(items.flatMap((item) => (item.draft ? [item.draft] : [])));
          setBatchItems(items);
        })}
      >
        <label>
          {t("batchLabel")}
          <input
            type="file"
            accept=".txt,text/plain"
            onChange={(event) => setBatchFile(event.target.files?.[0] ?? null)}
          />
        </label>
        <p>{t("batchHint")}</p>
        <button type="submit" disabled={working || batchFile === null}>
          {busy === "batch" ? t("working") : t("batchSubmit")}
        </button>
        {errorFor("batch")}
        {batchItems !== null && (
          <div>
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
