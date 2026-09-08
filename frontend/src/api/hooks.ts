import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, getApiError, type Page } from "./client";
import { getMessage } from "./message";

/** Minimal shape both `api` (ERP) and `gatewayApi` (WeCom gateway) satisfy. */
export interface ListFetcher {
  get<T>(url: string, params?: Record<string, unknown>): Promise<T>;
}

/**
 * Fetch a paginated list. Params other than page/page_size are passed through
 * as query params; changing them resets to page 1 automatically is NOT done —
 * call `setPage(1)` yourself when filters change if you want that behaviour.
 *
 * const list = useList<Order>("/orders", { status: "confirmed", q });
 * list.items / list.total / list.loading / list.setPage / list.refresh
 */
export function useList<T>(
  url: string | null,
  params?: Record<string, unknown>,
  fetcher: ListFetcher = api,
) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [data, setData] = useState<Page<T> | null>(null);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);
  const paramsKey = JSON.stringify(params ?? {});

  const refresh = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    if (!url) {
      setData(null);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    fetcher
      .get<Page<T>>(url, { ...JSON.parse(paramsKey), page, page_size: pageSize })
      .then((res) => {
        if (!cancelled) setData(res);
      })
      .catch(() => {
        if (!cancelled) setData(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [url, paramsKey, page, pageSize, nonce, fetcher]);

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
 * detail.data / detail.loading / detail.refresh
 */
export function useDetail<T>(url: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);

  const refresh = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    if (!url) {
      setData(null);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    api
      .get<T>(url)
      .then((res) => {
        if (!cancelled) setData(res);
      })
      .catch(() => {
        if (!cancelled) setData(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [url, nonce]);

  return { data, loading, refresh };
}

export interface MutateOptions {
  /** Custom success message; defaults to common.success translation. */
  success?: string;
  /** Called after a successful mutation (e.g. refresh a list). */
  onSuccess?: () => void;
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
        message?.success(options?.success ?? t("common.success"));
        options?.onSuccess?.();
        return true;
      } catch (error) {
        message?.error(getApiError(error) ?? t("common.error"));
        return false;
      } finally {
        setLoading(false);
      }
    },
    [t],
  );

  return { loading, run };
}
