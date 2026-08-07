/** Tiny in-memory TTL cache for tab warm / first paint. Not React Query. */

type Entry = { value: unknown; at: number };

const store = new Map<string, Entry>();

/** Soft cap — drop oldest entries when over (personal PWA RAM insurance). */
const MAX_KEYS = 120;

/**
 * Write generation per key. Bumped on delete / force-fetch so a slower in-flight
 * warm/SWR cannot overwrite a fresher manual refresh.
 */
const writeEpoch = new Map<string, number>();

function bumpEpoch(key: string): number {
  const n = (writeEpoch.get(key) ?? 0) + 1;
  writeEpoch.set(key, n);
  return n;
}

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
  bumpEpoch(key);
}

export function cacheClear(): void {
  store.clear();
  inflight.clear();
  writeEpoch.clear();
}

/** Test / diagnostics */
export function cacheSize(): number {
  return store.size;
}

const inflight = new Map<string, Promise<unknown>>();

function sharedFetch<T>(key: string, fetcher: () => Promise<T>): Promise<T> {
  const pending = inflight.get(key) as Promise<T> | undefined;
  if (pending) return pending;

  const epoch = writeEpoch.get(key) ?? 0;
  // let + definite assignment: finally must compare the same Promise instance
  let run!: Promise<T>;
  run = (async () => {
    try {
      const value = await fetcher();
      if ((writeEpoch.get(key) ?? 0) === epoch) {
        cacheSet(key, value);
      }
      return value;
    } finally {
      if (inflight.get(key) === run) inflight.delete(key);
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
 * Always hit the network. Wins over in-flight warm/SWR for the same key
 * (pull-to-refresh / resume force paths).
 */
export async function cacheForceFetch<T>(
  key: string,
  fetcher: () => Promise<T>,
): Promise<T> {
  const epoch = bumpEpoch(key);
  store.delete(key);
  inflight.delete(key);
  const value = await fetcher();
  if ((writeEpoch.get(key) ?? 0) === epoch) {
    cacheSet(key, value);
  }
  return value;
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
