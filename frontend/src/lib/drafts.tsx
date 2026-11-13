"use client";

import { createContext, type ReactNode, useCallback, useContext, useEffect, useState } from "react";

import type { RecipeDraft } from "@/lib/api";

type DraftQueue = {
  drafts: RecipeDraft[];
  addDrafts: (drafts: RecipeDraft[]) => void;
  removeFirst: () => void;
};

const DraftContext = createContext<DraftQueue | null>(null);

/**
 * Holds drafts in memory only: drafts are not stored until the user saves them,
 * so a reload loses them. The browser warns before that happens.
 */
export function DraftsProvider({
  children,
  initialDrafts = [],
}: {
  children: ReactNode;
  initialDrafts?: RecipeDraft[];
}) {
  const [drafts, setDrafts] = useState<RecipeDraft[]>(initialDrafts);

  const addDrafts = useCallback(
    (added: RecipeDraft[]) => setDrafts((current) => [...current, ...added]),
    [],
  );
  const removeFirst = useCallback(() => setDrafts((current) => current.slice(1)), []);

  useEffect(() => {
    if (drafts.length === 0) {
      return;
    }
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [drafts.length]);

  return (
    <DraftContext.Provider value={{ drafts, addDrafts, removeFirst }}>
      {children}
    </DraftContext.Provider>
  );
}

export function useDrafts(): DraftQueue {
  const queue = useContext(DraftContext);
  if (queue === null) {
    throw new Error("useDrafts must be used inside DraftsProvider");
  }
  return queue;
}
