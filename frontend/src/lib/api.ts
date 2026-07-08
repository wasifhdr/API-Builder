const BASE = '/api'

export class ApiError extends Error {
  status: number
  /** The raw `detail` value from the response body — a string for simple
   * errors, or a structured object (e.g. `{detail, shortfall_bdt}`) for
   * errors that carry extra data like insufficient-balance shortfalls. */
  body: unknown

  constructor(status: number, message: string, body?: unknown) {
    super(message)
    this.status = status
    this.body = body
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
    ...init,
  })

  if (!res.ok) {
    const json = await res.json().catch(() => ({}))
    const detail = json.detail
    const message = typeof detail === 'string' ? detail : (detail?.detail ?? res.statusText)
    throw new ApiError(res.status, message, detail)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  patch: <T>(path: string, body: unknown) =>
    request<T>(path, { method: 'PATCH', body: JSON.stringify(body) }),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: body ? JSON.stringify(body) : undefined }),
  delete: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'DELETE', body: body ? JSON.stringify(body) : undefined }),
}
