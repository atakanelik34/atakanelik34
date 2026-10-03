import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, apiRequest, setUnauthorizedHandler } from '@/lib/api'
import { session } from '@/lib/session'

function mockFetch(response: Response) {
  return vi.spyOn(globalThis, 'fetch').mockResolvedValue(response)
}

function problem(status: number, body: object) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/problem+json' },
  })
}

afterEach(() => {
  session.clear()
  setUnauthorizedHandler(() => session.clear())
})

describe('apiRequest', () => {
  it('prefixes the API path and attaches the bearer token', async () => {
    session.setToken('tok-123')
    const fetchSpy = mockFetch(new Response(JSON.stringify({ ok: true }), { status: 200 }))

    await expect(apiRequest<{ ok: boolean }>('/auth/me')).resolves.toEqual({ ok: true })

    const [url, init] = fetchSpy.mock.calls[0]!
    expect(url).toBe('/api/v1/auth/me')
    expect(((init?.headers ?? {}) as Record<string, string>).Authorization).toBe('Bearer tok-123')
  })

  it('does not send the token for anonymous requests', async () => {
    session.setToken('tok-123')
    const fetchSpy = mockFetch(new Response('{}', { status: 200 }))
    await apiRequest('/auth/login', { method: 'POST', body: {}, anonymous: true })
    const init = fetchSpy.mock.calls[0]![1]
    expect(((init?.headers ?? {}) as Record<string, string>).Authorization).toBeUndefined()
  })

  it('maps problem+json into a classified ApiError', async () => {
    mockFetch(
      problem(409, {
        detail: 'A user with this email already exists',
        error_category: 'BUSINESS_ERROR',
        code: 'conflict',
        correlation_id: 'abc123',
      }),
    )
    const error = await apiRequest('/users', { method: 'POST', body: {} }).catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({
      status: 409,
      category: 'BUSINESS_ERROR',
      code: 'conflict',
      correlationId: 'abc123',
      message: 'A user with this email already exists',
    })
  })

  it('signs the user out on 401 for authenticated requests only', async () => {
    const onUnauthorized = vi.fn<() => void>()
    setUnauthorizedHandler(onUnauthorized)

    mockFetch(problem(401, { error_category: 'AUTHENTICATION_ERROR' }))
    await expect(apiRequest('/auth/me')).rejects.toBeInstanceOf(ApiError)
    expect(onUnauthorized).toHaveBeenCalledTimes(1)

    mockFetch(problem(401, { error_category: 'AUTHENTICATION_ERROR' }))
    await expect(apiRequest('/auth/login', { anonymous: true })).rejects.toBeInstanceOf(ApiError)
    expect(onUnauthorized).toHaveBeenCalledTimes(1)
  })

  it('turns gateway error pages into a readable NETWORK_ERROR', async () => {
    mockFetch(new Response('<html>502 Bad Gateway</html>', { status: 502 }))
    await expect(apiRequest('/auth/me')).rejects.toMatchObject({
      category: 'NETWORK_ERROR',
      message: 'The service is temporarily unavailable. Try again shortly.',
    })
  })

  it('reports network failures as NETWORK_ERROR', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('Failed to fetch'))
    await expect(apiRequest('/health/live')).rejects.toMatchObject({ category: 'NETWORK_ERROR' })
  })
})
