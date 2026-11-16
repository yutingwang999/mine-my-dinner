"use client";

import { useTranslations } from "next-intl";
import { type FormEvent, type KeyboardEvent, type RefObject, useLayoutEffect, useRef } from "react";

type Props = {
  busy: boolean;
  onSend: () => void;
  value: string;
  onChange: (value: string) => void;
  /** Lets the page focus the box after an example fills it. */
  textareaRef?: RefObject<HTMLTextAreaElement | null>;
};

export function Composer({ busy, onSend, value, onChange, textareaRef }: Props) {
  const t = useTranslations("chat");
  const ownRef = useRef<HTMLTextAreaElement | null>(null);
  const boxRef = textareaRef ?? ownRef;
  // keyCode 229 is not usable: a Chinese IME in Chinese mode reports it for nearly every key.
  const composing = useRef(false);
  // Safari fires compositionend before the keydown of the Enter that confirms the candidate.
  const justEnded = useRef(false);
  const canSend = !busy && value.trim() !== "";

  // Grows with the text; the CSS max-height caps it at a few lines.
  useLayoutEffect(() => {
    const box = boxRef.current;
    if (!box) return;
    box.style.height = "auto";
    // scrollHeight leaves out the border, which border-box sizing counts.
    box.style.height = `${box.scrollHeight + box.offsetHeight - box.clientHeight}px`;
  }, [value, boxRef]);

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey) return;
    // Enter that confirms an IME candidate must not send.
    if (event.nativeEvent.isComposing || composing.current || justEnded.current) return;
    event.preventDefault();
    if (canSend) onSend();
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (canSend) onSend();
  }

  return (
    <form className="composer" onSubmit={onSubmit}>
      <textarea
        ref={boxRef}
        rows={1}
        value={value}
        maxLength={4000}
        aria-label={t("inputLabel")}
        placeholder={t("inputPlaceholder")}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={onKeyDown}
        onCompositionStart={() => {
          composing.current = true;
        }}
        onCompositionEnd={() => {
          composing.current = false;
          justEnded.current = true;
          setTimeout(() => {
            justEnded.current = false;
          }, 0);
        }}
      />
      <button type="submit" className="composer-action btn-primary" disabled={!canSend}>
        {t("send")}
      </button>
    </form>
  );
}
