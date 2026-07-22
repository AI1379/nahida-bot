export function createTypedStorage<T>(
  storageKey: string,
  sanitize: (value: unknown) => T,
) {
  function read(): T {
    if (typeof window === "undefined") return sanitize(null);
    try {
      const raw = window.localStorage.getItem(storageKey);
      return sanitize(raw ? JSON.parse(raw) : null);
    } catch {
      return sanitize(null);
    }
  }

  function write(value: T): void {
    if (typeof window === "undefined") return;
    try {
      window.localStorage.setItem(storageKey, JSON.stringify(sanitize(value)));
    } catch {
      // Legacy storage is only a migration/fallback path. The canonical
      // persistence layer reports write failures through the Desktop store.
    }
  }

  function clear(): void {
    if (typeof window === "undefined") return;
    try {
      window.localStorage.removeItem(storageKey);
    } catch {
      // Storage can be unavailable in privacy-restricted WebViews.
    }
  }

  return { read, write, clear };
}
