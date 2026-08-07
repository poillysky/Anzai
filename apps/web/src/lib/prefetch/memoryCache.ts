/** Tiny in-memory TTL cache for tab warm / first paint. Not React Query. */

type Entry = { value: unknown; at: number };

const store = new Map<string, Entry>();

/** Soft cap — drop oldest entries when over (personal PWA RAM insurance). */
const MAX_KEYS = 120;

/** Fresh within ttl — does NOT delete stale entries (keep for peek / SWR). */
export function cacheGet<T>(key: string, ttlMs: number): T | null {
  const hit = store.get(key);
  if (!hit) return null;
  if (Date.now() - hit.at > ttlMs) return null;
  return hit.value as T;
}

/** Last known value regardless of TTL. */
export function cachePeek<T>(key: string): T | null {
  const hit = store.get(key);
  return hit ? (hit.value as T) : null;
}

/** Age in ms, or null if missing. */
export function cacheAge(key: string): number | null {
  const hit = store.get(key);
  if (!hit) return null;
  return Date.now() - hit.at;
}

function evictIfNeeded(preserveKey?: string): void {
  if (store.size <= MAX_KEYS) return;
  const entries = [...store.entries()].sort((a, b) => a[1].at - b[1].at);
  const overflow = store.size - MAX_KEYS;
  let dropped = 0;
  for (const [k] of entries) {
    if (dropped >= overflow) break;
    if (preserveKey && k === preserveKey) continue;
    store.delete(k);
    inflight.delete(k);
    dropped += 1;
  }
}

export function cacheSet<T>(key: string, value: T): void {
  store.set(key, { value, at: Date.now() });
  evictIfNeeded(key);
}

/** Drop one key (and any in-flight fetch for it). */
export function cacheDelete(key: string): void {
  store.delete(key);
  inflight.delete(key);
}

export function cacheClear(): void {
  store.clear();
  inflight.clear();
}

/** Test / diagnostics */
export function cacheSize(): number {
  return store.size;
}

const inflight = new Map<string, Promise<unknown>>();

function sharedFetch<T>(key: string, fetcher: () => Promise<T>): Promise<T> {
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

/** Return fresh cache or fetch + store. Concurrent callers share one in-flight promise. */
export async function cacheFetch<T>(
  key: string,
  fetcher: () => Promise<T>,
  ttlMs: number,
): Promise<T> {
  const cached = cacheGet<T>(key, ttlMs);
  if (cached !== null) return cached;
  return sharedFetch(key, fetcher);
}

/**
 * Stale-while-revalidate: return peek immediately (even if stale), refresh in background.
 * Miss → await fetch. Fresh → return without network.
 */
export async function cacheSWR<T>(
  key: string,
  fetcher: () => Promise<T>,
  ttlMs: number,
  onUpdate?: (value: T) => void,
): Promise<T> {
  const stale = cachePeek<T>(key);
  const fresh = cacheGet<T>(key, ttlMs);

  if (fresh !== null) {
    onUpdate?.(fresh);
    return fresh;
  }

  const run = sharedFetch(key, fetcher);
  if (onUpdate) {
    void run.then((v) => onUpdate(v)).catch(() => {});
  }

  if (stale !== null) {
    onUpdate?.(stale);
    return stale;
  }
  return run;
}
