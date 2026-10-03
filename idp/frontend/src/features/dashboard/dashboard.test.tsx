import { screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { DashboardPage } from '@/features/dashboard/DashboardPage'
import { session } from '@/lib/session'
import { json, renderRoutes } from '@/test/render'

describe('dashboard', () => {
  it('shows the processing overview to document readers without system access', async () => {
    session.setToken('t')
    vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
      const url = String(input)
      if (url.endsWith('/auth/me')) {
        return Promise.resolve(
          json({
            user: { id: 'u', email: 'a@b.c', full_name: 'A', role: 'reviewer', is_active: true, last_login_at: null, created_at: '' },
            tenant: { id: 't', slug: 'acme', name: 'Acme' },
            permissions: ['documents:read'],
          }),
        )
      }
      return Promise.resolve(
        json({
          window_days: 30,
          documents: { COMPLETED: 8, PROCESSING: 1, READY_FOR_ACTION: 2 },
          jobs: { SUCCEEDED: 8, FAILED: 1, DEAD_LETTERED: 1 },
          open_reviews: 3,
          straight_through_rate: 0.75,
          avg_processing_ms: 4200,
          llm: { calls: 12, cost: 0.42, tokens: 9000 },
          actions: { succeeded: 5, failed: 1 },
        }),
      )
    })
    renderRoutes([{ path: '/', element: <DashboardPage /> }], '/')
    expect(await screen.findByText('75%')).toBeInTheDocument()
    expect(screen.getByText('11')).toBeInTheDocument()
    expect(screen.getByText('2 awaiting action approval')).toBeInTheDocument()
    expect(screen.getByText('Your role does not include access to system status.')).toBeInTheDocument()
    session.clear()
  })
})
