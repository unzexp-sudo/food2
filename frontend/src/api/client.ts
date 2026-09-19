import axios from "axios";

/** Standard paginated list response (AGENT_CONTRACTS §2 pagination convention). */
export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export const TOKEN_KEY = "erp_token";
export const USER_KEY = "erp_user";

const client = axios.create({
  baseURL: "/api/v1",
});

// Attach Bearer token to every request.
client.interceptors.request.use((config) => {
  const token = localStorage.getItem(TOKEN_KEY);
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// On 401: clear session and redirect to /login.
client.interceptors.response.use(
  (response) => response,
  (error) => {
    if (axios.isAxiosError(error) && error.response?.status === 401) {
      localStorage.removeItem(TOKEN_KEY);
      localStorage.removeItem(USER_KEY);
      if (window.location.pathname !== "/login") {
        window.location.assign("/login");
      }
    }
    return Promise.reject(error);
  },
);

/** Typed helpers — always use these, never `client` directly from screens. */
export const api = {
  get<T>(url: string, params?: Record<string, unknown>): Promise<T> {
    return client.get<T>(url, { params }).then((res) => res.data);
  },
  post<T>(url: string, data?: unknown): Promise<T> {
    return client.post<T>(url, data).then((res) => res.data);
  },
  /**
   * POST a multipart form (file uploads).
   *
   * Deliberately does not set `Content-Type`: the browser has to generate the
   * multipart boundary, and setting the header by hand drops it — the request
   * then arrives with a body FastAPI cannot parse and the endpoint reports the
   * file as missing. axios sets the header correctly when handed a FormData.
   */
  postForm<T>(url: string, form: FormData): Promise<T> {
    return client.post<T>(url, form).then((res) => res.data);
  },
  patch<T>(url: string, data?: unknown): Promise<T> {
    return client.patch<T>(url, data).then((res) => res.data);
  },
  put<T>(url: string, data?: unknown): Promise<T> {
    return client.put<T>(url, data).then((res) => res.data);
  },
  delete<T>(url: string): Promise<T> {
    return client.delete<T>(url).then((res) => res.data);
  },
};

/**
 * WeCom Gateway client (docs/WECOM_CONTRACTS.md).
 *
 * The gateway is a separate service and is NOT behind the Vite `/api` proxy, so
 * it needs its own absolute baseURL. Override with `VITE_WECOM_GATEWAY_URL`.
 */
const configuredGatewayUrl =
  (import.meta.env.VITE_WECOM_GATEWAY_URL as string | undefined)?.trim().replace(/\/$/, "") ||
  undefined;

/**
 * True when this bundle was compiled without `VITE_WECOM_GATEWAY_URL`.
 *
 * Vite inlines `import.meta.env.VITE_*` at **build** time, so this is a property
 * of the compiled bundle and not of the running service: setting the variable on
 * a deployed service at runtime changes nothing, and redeploying without it
 * bakes in the fallback below. That fallback is a loopback address — correct for
 * `npm run dev` on a laptop, and unreachable for every real user of a deployed
 * build.
 *
 * The distinction is invisible at runtime, which is what makes it dangerous:
 * production ran this way for as long as the WeCom pages have existed, and the
 * only symptom was an alert telling the operator to start a server on their own
 * machine. Exported so the screens can name the actual cause instead.
 */
export const WECOM_GATEWAY_URL_IS_UNCONFIGURED = configuredGatewayUrl === undefined;

export const WECOM_GATEWAY_URL: string = configuredGatewayUrl ?? "http://127.0.0.1:8100";

const gatewayClient = axios.create({
  baseURL: WECOM_GATEWAY_URL,
});

/**
 * Typed helpers for `WECOM_GATEWAY_URL/wecom/*`.
 * The gateway's admin endpoints are unauthenticated in this deployment, so no
 * Bearer token is attached — do not reuse this client for the ERP.
 */
export const gatewayApi = {
  get<T>(url: string, params?: Record<string, unknown>): Promise<T> {
    return gatewayClient.get<T>(url, { params }).then((res) => res.data);
  },
  post<T>(url: string, data?: unknown): Promise<T> {
    return gatewayClient.post<T>(url, data).then((res) => res.data);
  },
  patch<T>(url: string, data?: unknown): Promise<T> {
    return gatewayClient.patch<T>(url, data).then((res) => res.data);
  },
  put<T>(url: string, data?: unknown): Promise<T> {
    return gatewayClient.put<T>(url, data).then((res) => res.data);
  },
  delete<T>(url: string): Promise<T> {
    return gatewayClient.delete<T>(url).then((res) => res.data);
  },
};

/**
 * Extract a human-readable error message from an API error.
 * FastAPI errors put the message in `response.data.detail`.
 */
export function getApiError(error: unknown): string | null {
  if (axios.isAxiosError(error)) {
    const detail = (error.response?.data as { detail?: unknown } | undefined)?.detail;
    if (typeof detail === "string") return detail;
    return error.message;
  }
  return null;
}

export default client;
