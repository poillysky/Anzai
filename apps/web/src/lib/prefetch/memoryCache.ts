/** Tiny in-memory TTL cache for tab warm / first paint. Not React Query. */

type Entry = { value: unknown; at: number };

const store = new Map<string, Entry>();

export function cacheGet<T>(key: string, ttlMs: number): T | null {
  const hit = store.get(key);
  if (!hit) return null;
  if (Date.now() - hit.at > ttlMs) {
    store.delete(key);
    return null;
  }
  return hit.value as T;
}

export function cachePeek<T>(key: string): T | null {
  const hit = store.get(key);
  return hit ? (hit.value as T) : null;
}

export function cacheSet<T>(key: string, value: T): void {
  store.set(key, { value, at: Date.now() });
}

export function cacheClear(): void {
  store.clear();
}

/** Return fresh cache or fetch + store. Concurrent callers share one in-flight promise. */
const inflight = new Map<string, Promise<unknown>>();

export async function cacheFetch<T>(
  key: string,
  fetcher: () => Promise<T>,
  ttlMs: number,
): Promise<T> {
  const cached = cacheGet<T>(key, ttlMs);
  if (cached !== null) return cached;

  const pending = inflight.get(key) as Promise<T> | undefined;
  if (pending) return pending;

  const run = (async () => {
    try {
      const value = await fetcher();
      cacheSet(key, value);
      return value;
    } finally {
      inflight.delete(key);
    }
  })();

  inflight.set(key, run);
  return run;
}
