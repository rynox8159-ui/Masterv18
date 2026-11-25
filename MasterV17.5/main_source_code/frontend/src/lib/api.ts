const BASE = `${import.meta.env.VITE_BACKEND_URL || ''}/api`;
export const AUTH_EVENT = 'naqso:session-required';

export const apiUrl = (path: string) => `${BASE}${path}`;
export const streamUrl = (path: string) => apiUrl(path);

// Fields are declared, not constructor parameter properties: tsconfig sets
// erasableSyntaxOnly, which rejects `constructor(readonly status: number)`.
export class ApiError extends Error {
  status: number;
  body: unknown;

  constructor(status: number, body: unknown) {
    super(`request failed with ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

type JsonBody = unknown;
type ApiHeaders = Record<string, string>;

async function request<T>(method: string, path: string, body?: JsonBody, extraHeaders?: ApiHeaders): Promise<T> {
  const headers = { ...(body === undefined ? {} : { "Content-Type": "application/json" }), ...extraHeaders };
  const res = await fetch(apiUrl(path), {
    method,
    headers,
    credentials: 'include',
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (!res.ok) {
    const errBody = await res.json().catch(() => null);
    if (res.status === 401 && errBody && typeof errBody === 'object' && 'detail' in errBody && errBody.detail === 'SESSION_REQUIRED') {
      window.dispatchEvent(new Event(AUTH_EVENT));
    }
    throw new ApiError(res.status, errBody);
  }

  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const apiGet = <T>(path: string) => request<T>("GET", path);
export const apiPost = <T>(path: string, body?: JsonBody, headers?: ApiHeaders) => request<T>("POST", path, body ?? null, headers);
export const apiPut = <T>(path: string, body?: JsonBody) => request<T>("PUT", path, body ?? null);
export const apiPatch = <T>(path: string, body?: JsonBody) =>
  request<T>("PATCH", path, body ?? null);
export const apiDelete = <T>(path: string) => request<T>("DELETE", path);

export function promptOperatorControlHeaders(): ApiHeaders | null {
  if (localStorage.getItem("operatorKeyRequired") !== "1") return {};
  const key = window.prompt("Operator key (PROVIDER_CONTROL_KEY from main_source_code/backend/.env)");
  return key ? { "X-Provider-Control-Key": key } : null;
}
