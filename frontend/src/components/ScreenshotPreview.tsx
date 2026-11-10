"use client";

import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";

/** Shows screenshots still held in the browser, before they are saved with the recipe. */
export function ScreenshotPreview({ files }: { files: File[] }) {
  const t = useTranslations("library");
  const [urls, setUrls] = useState<string[]>([]);

  useEffect(() => {
    // Test environments have no object URLs; the review still works without previews.
    if (typeof URL.createObjectURL !== "function") return;
    const created = files.map((file) => URL.createObjectURL(file));
    const frame = requestAnimationFrame(() => setUrls(created));
    return () => {
      cancelAnimationFrame(frame);
      created.forEach((url) => URL.revokeObjectURL(url));
    };
  }, [files]);

  return (
    <div className="screenshots">
      {urls.map((url, index) => (
        // eslint-disable-next-line @next/next/no-img-element -- local object URLs, not optimizable
        <img key={url} src={url} alt={t("screenshotAlt", { number: index + 1 })} />
      ))}
    </div>
  );
}
