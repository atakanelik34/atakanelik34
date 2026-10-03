import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { LoginPage } from '@/features/auth/LoginPage'
import { RequireAuth } from '@/features/auth/RequireAuth'
import { session } from '@/lib/session'
import { json, renderRoutes } from '@/test/render'

const routes = [
  { path: '/login', element: <LoginPage /> },
  {
    path: '/',
    element: (
      <RequireAuth>
        <p>Protected home</p>
      </RequireAuth>
    ),
  },
]

describe('login flow', () => {
  it('redirects unauthenticated users to the login page', () => {
    renderRoutes(routes, '/')
    expect(screen.getByRole('heading', { name: 'Sign in' })).toBeInTheDocument()
  })

  it('stores the token and returns to the app on success', async () => {
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(json({ access_token: 'jwt-abc', token_type: 'bearer', expires_at: '' }))
    renderRoutes(routes, '/')

    await userEvent.type(screen.getByLabelText('Email'), 'owner@acme.test')
    await userEvent.type(screen.getByLabelText('Password'), 'Owner-Password-123!')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByText('Protected home')).toBeInTheDocument()
    expect(session.getToken()).toBe('jwt-abc')
    const body = JSON.parse(fetchSpy.mock.calls[0]![1]!.body as string) as Record<string, string>
    expect(body).toEqual({ email: 'owner@acme.test', password: 'Owner-Password-123!' })
    session.clear()
  })

  it('shows the classified error and clears the password on failure', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      json(
        {
          detail: 'Invalid email or password',
          error_category: 'AUTHENTICATION_ERROR',
          correlation_id: 'cid-1',
        },
        401,
      ),
    )
    renderRoutes(routes, '/login')

    await userEvent.type(screen.getByLabelText('Email'), 'owner@acme.test')
    await userEvent.type(screen.getByLabelText('Password'), 'wrong')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid email or password')
    expect(screen.getByRole('alert')).toHaveTextContent('ref cid-1')
    await waitFor(() => expect(screen.getByLabelText('Password')).toHaveValue(''))
    expect(session.getToken()).toBeNull()
  })
})
