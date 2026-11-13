import { type DependencyList, useEffect, useState } from "react";

/**
 * Loads data when the dependencies change. `reload` fetches again, such as after an
 * edit. A response for outdated dependencies is ignored.
 */
export function useLoad<T>(load: () => Promise<T>, deps: DependencyList) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    let active = true;
    load()
      .then((result) => {
        if (active) {
          setData(result);
          setError(null);
        }
      })
      .catch((caught) => active && setError(caught));
    return () => {
      active = false;
    };
    // The caller's deps decide when to load; `load` itself changes every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, version]);

  return { data, error, reload: () => setVersion((current) => current + 1) };
}
