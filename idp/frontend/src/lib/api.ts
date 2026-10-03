import { session } from '@/lib/session'

export const API_PREFIX = '/api/v1'

/** Mirrors the backend's ErrorCategory (ARCHITECTURE.md §11). */
export type ErrorCategory =
  | 'SYSTEM_ERROR'
  | 'PROVIDER_ERROR'
  | 'DOCUMENT_ERROR'
  | 'VALIDATION_ERROR'
  | 'BUSINESS_ERROR'
  | 'AUTHENTICATION_ERROR'
  | 'AUTHORIZATION_ERROR'
  | 'NOT_FOUND'
  | 'CONFIGURATION_ERROR'
  | 'RATE_LIMITED'
  | 'NETWORK_ERROR'

interface ProblemBody {
  detail?: string
  error_category?: ErrorCategory
  code?: string
  correlation_id?: string | null
  errors?: { loc: (string | number)[]; msg: string }[]
}

export class ApiError extends Error {
  readonly status: number
  readonly category: ErrorCategory
  readonly code: string
  readonly correlationId: string | null
  readonly fieldErrors: { loc: (string | number)[]; msg: string }[]

  constructor(status: number, body: ProblemBody) {
    super(body.detail ?? `Request failed with status ${status}`)
    this.name = 'ApiError'
    this.status = status
    this.category = body.error_category ?? 'SYSTEM_ERROR'
    this.code = body.code ?? 'unknown'
    this.correlationId = body.correlation_id ?? null
    this.fieldErrors = body.errors ?? []
  }
}

type UnauthorizedHandler = () => void
let onUnauthorized: UnauthorizedHandler = () => session.clear()

export function setUnauthorizedHandler(handler: UnauthorizedHandler): void {
  onUnauthorized = handler
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  body?: unknown
  signal?: AbortSignal
  /** Skip attaching the bearer token (login). */
  anonymous?: boolean
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (options.body !== undefined) headers['Content-Type'] = 'application/json'
  const token = session.getToken()
  if (token && !options.anonymous) headers.Authorization = `Bearer ${token}`

  let response: Response
  try {
    response = await fetch(`${API_PREFIX}${path}`, {
      method: options.method ?? 'GET',
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: options.signal,
      credentials: 'same-origin',
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new ApiError(0, {
      detail: 'Unable to reach the server',
      error_category: 'NETWORK_ERROR',
      code: 'network_error',
    })
  }

  if (response.ok) {
    if (response.status === 204) return undefined as T
    return (await response.json()) as T
  }

  let body: ProblemBody = {}
  try {
    body = (await response.json()) as ProblemBody
  } catch {
    // Non-JSON body: the request never reached the API (proxy/gateway error page).
    if (response.status >= 502 && response.status <= 504) {
      body = {
        detail: 'The service is temporarily unavailable. Try again shortly.',
        error_category: 'NETWORK_ERROR',
        code: 'gateway_error',
      }
    }
  }
  const error = new ApiError(response.status, body)
  if (response.status === 401 && !options.anonymous) onUnauthorized()
  throw error
}
