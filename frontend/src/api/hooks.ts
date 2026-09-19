import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, getApiError, type Page } from "./client";
import { getMessage } from "./message";

/**
 * How often an operational list refetches itself, and how often the nav badges
 * do. 15s is the compromise: work arrives from WeCom (a webhook, not a user
 * action) while a tab is open, and a screen that only updates on reload is a
 * snapshot of the past. It is deliberately NOT a websocket — see the note on
 * `useList`'s `pollMs`.
 */
export const LIST_POLL_MS = 15_000;
export const BADGE_POLL_MS = 15_000;

/** Minimal shape both `api` (ERP) and `gatewayApi` (WeCom gateway) satisfy. */
export interface ListFetcher {
  get<T>(url: string, params?: Record<string, unknown>): Promise<T>;
}

export interface UseListOptions {
  /**
   * Poll interval in ms. A poll refetches **silently**: it does not raise
   * `loading`, and a failed poll keeps the rows already on screen instead of
   * blanking the table. A refresh that flashes a spinner every 15s, or that
   * empties the list because one request timed out, is worse than no refresh.
   *
   * Polling rather than SSE/websockets on purpose: the whole app is already
   * request/response behind one auth header, there is no connection to keep
   * alive through the platform proxy, and the failure mode of a dropped poll
   * is "one stale tick" instead of "silently dead stream".
   */
  pollMs?: number;
  /** List endpoint; defaults to the ERP client. */
  fetcher?: ListFetcher;
}

export interface UseDetailOptions {
  /** Poll interval in ms; silent, exactly as `useList`'s `pollMs`. */
  pollMs?: number;
}

/**
 * Fetch a paginated list. Params other than page/page_size are passed through
 * as query params; changing them resets to page 1 automatically is NOT done —
 * call `setPage(1)` yourself when filters change if you want that behaviour.
 *
 * const list = useList<Order>("/orders", { status: "confirmed", q });
 * const live = useList<Order>("/orders", params, { pollMs: LIST_POLL_MS });
 * list.items / list.total / list.loading / list.setPage / list.refresh
 */
export function useList<T>(
  url: string | null,
  params?: Record<string, unknown>,
  fetcherOrOptions: ListFetcher | UseListOptions = api,
) {
  // Third argument accepts either a fetcher (the original signature, still used
  // by the WeCom gateway pages) or an options object. Both are accepted so the
  // 20 existing call sites did not have to change to gain polling.
  const options: UseListOptions =
    typeof (fetcherOrOptions as ListFetcher | undefined)?.get === "function"
      ? { fetcher: fetcherOrOptions as ListFetcher }
      : (fetcherOrOptions as UseListOptions);
  const fetcher = options.fetcher ?? api;
  const pollMs = options.pollMs ?? 0;

  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [data, setData] = useState<Page<T> | null>(null);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);
  const paramsKey = JSON.stringify(params ?? {});
  // Set by the poll timer just before it bumps the nonce, read (and cleared) by
  // the fetch effect. A ref rather than state because it must not itself cause
  // a render — it is a note to the next effect run, not data.
  const silentRef = useRef(false);

  const refresh = useCallback(() => {
    silentRef.current = false;
    setNonce((n) => n + 1);
  }, []);
  const refreshSilently = useCallback(() => {
    silentRef.current = true;
    setNonce((n) => n + 1);
  }, []);

  useEffect(() => {
    if (!url) {
      setData(null);
      setLoading(false);
      return;
    }
    let cancelled = false;
    const silent = silentRef.current;
    silentRef.current = false;
    if (!silent) setLoading(true);
    fetcher
      .get<Page<T>>(url, { ...JSON.parse(paramsKey), page, page_size: pageSize })
      .then((res) => {
        if (!cancelled) setData(res);
      })
      .catch(() => {
        // A failed poll keeps what is on screen: the rows are still the best
        // information available, and blanking them would look like the work
        // vanished.
        if (!cancelled && !silent) setData(null);
      })
      .finally(() => {
        if (!cancelled && !silent) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [url, paramsKey, page, pageSize, nonce, fetcher]);

  useEffect(() => {
    if (!pollMs || !url) return;
    const id = setInterval(() => {
      // Hidden tabs do not poll. Coming back refetches immediately instead, so
      // returning to the app is never up to 15s stale.
      if (typeof document !== "undefined" && document.hidden) return;
      refreshSilently();
    }, pollMs);
    const onVisible = () => {
      if (!document.hidden) refreshSilently();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(id);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [pollMs, url, refreshSilently]);

  return {
    data,
    items: data?.items ?? [],
    total: data?.total ?? 0,
    loading,
    page,
    pageSize,
    setPage,
    setPageSize,
    refresh,
  };
}

/**
 * Fetch a single resource. Pass `null` (or a falsy id) to skip fetching.
 *
 * const detail = useDetail<Order>(id ? `/orders/${id}` : null);
 * const counts = useDetail<Counts>("/pick-lists/task-count", { pollMs: LIST_POLL_MS });
 * detail.data / detail.loading / detail.refresh
 */
export function useDetail<T>(url: string | null, options: UseDetailOptions = {}) {
  const pollMs = options.pollMs ?? 0;
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);
  const silentRef = useRef(false);

  const refresh = useCallback(() => {
    silentRef.current = false;
    setNonce((n) => n + 1);
  }, []);
  const refreshSilently = useCallback(() => {
    silentRef.current = true;
    setNonce((n) => n + 1);
  }, []);

  useEffect(() => {
    if (!url) {
      setData(null);
      setLoading(false);
      return;
    }
    let cancelled = false;
    const silent = silentRef.current;
    silentRef.current = false;
    if (!silent) setLoading(true);
    api
      .get<T>(url)
      .then((res) => {
        if (!cancelled) setData(res);
      })
      .catch(() => {
        // Same rule as `useList`: a failed poll must not erase a good reading.
        if (!cancelled && !silent) setData(null);
      })
      .finally(() => {
        if (!cancelled && !silent) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [url, nonce]);

  useEffect(() => {
    if (!pollMs || !url) return;
    const id = setInterval(() => {
      if (typeof document !== "undefined" && document.hidden) return;
      refreshSilently();
    }, pollMs);
    const onVisible = () => {
      if (!document.hidden) refreshSilently();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(id);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [pollMs, url, refreshSilently]);

  return { data, loading, refresh };
}

export interface MutateOptions {
  /**
   * Custom success message; defaults to common.success translation.
   *
   * Pass `false` to suppress the success toast entirely. Use that when only the
   * *response* knows what to say — a count, a list of skipped rows — because
   * this string is resolved when the call is constructed, before the response
   * exists, so a value assigned inside the async body would always be stale.
   * The caller then issues its own message after awaiting. Without this, the
   * only way to report a response-derived number was to abandon `run()`
   * altogether and re-implement its loading and error handling.
   */
  success?: string | false;
  /** Called after a successful mutation (e.g. refresh a list). */
  onSuccess?: () => void;
  /**
   * Called with the raw error when the mutation fails. Return a string to
   * replace the default toast, or void to keep it.
   *
   * This exists because `run()` deliberately reports failures as a toast and
   * then discards the error — which is fine for a failure with no addressable
   * cause, but wrong for one the form could point at. A caller that knows
   * *which field* the server refused (e.g. a duplicate customer code) uses
   * this to mark that field and still get the toast.
   */
  onError?: (error: unknown) => string | void;
}

/**
 * Wrap any POST/PATCH/DELETE call with loading state and AntD message feedback.
 *
 * const { loading, run } = useMutate();
 * const ok = await run(() => api.post(`/orders/${id}/confirm`), {
 *   onSuccess: detail.refresh,
 * });
 */
export function useMutate() {
  const { t } = useTranslation();
  const [loading, setLoading] = useState(false);

  const run = useCallback(
    async (fn: () => Promise<unknown>, options?: MutateOptions): Promise<boolean> => {
      const message = getMessage();
      setLoading(true);
      try {
        await fn();
        if (options?.success !== false) {
          message?.success(options?.success ?? t("common.success"));
        }
        options?.onSuccess?.();
        return true;
      } catch (error) {
        const override = options?.onError?.(error);
        message?.error(override ?? getApiError(error) ?? t("common.error"));
        return false;
      } finally {
        setLoading(false);
      }
    },
    [t],
  );

  return { loading, run };
}
