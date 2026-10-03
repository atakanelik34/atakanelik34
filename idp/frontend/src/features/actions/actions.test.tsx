import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { ActionsCard } from '@/features/actions/ActionsCard'
import { InboxPage } from '@/features/actions/InboxPage'
import { session } from '@/lib/session'
import { json, renderRoutes } from '@/test/render'

const ME = {
  user: { id: 'u', email: 'a@b.c', full_name: 'A', role: 'admin', is_active: true, last_login_at: null, created_at: '' },
  tenant: { id: 't', slug: 'acme', name: 'Acme' },
  permissions: ['documents:read', 'actions:execute', 'users:write'],
}

const RUN = {
  id: 'r1', job_id: 'j', part_id: 'p', name: 'post_invoice', connection_key: 'erp', kind: 'mock_erp', status: 'pending_approval',
  requires_approval: true, attempts: 0, payload: { invoice_number: 'INV-1' }, external_reference: null, error_code: null, is_mock: true,
  decided_by_id: null, decided_at: null, decision_note: null, executed_at: null, created_at: '2026-10-01T10:00:00Z',
}

describe('actions card', () => {
  it('shows the payload and requires a reason to reject', async () => {
    session.setToken('t')
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
      const url = String(input)
      if (url.endsWith('/auth/me')) return Promise.resolve(json(ME))
      if (url.endsWith('/actions/approve')) return Promise.resolve(json([{ ...RUN, status: 'approved' }]))
      return Promise.resolve(json([RUN]))
    })
    renderRoutes([{ path: '/', element: <ActionsCard documentId="d1" awaitingApproval /> }], '/')
    expect(await screen.findByText('post_invoice')).toBeInTheDocument()
    expect(screen.getByText('mock')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Show payload' }))
    expect(screen.getByText(/INV-1/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Reject actions' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Approve actions' }))
    await vi.waitFor(() => expect(fetchSpy.mock.calls.some(([u]) => String(u).endsWith('/documents/d1/actions/approve'))).toBe(true))
    session.clear()
  })
})

describe('inbox', () => {
  it('lists API-ingested documents and shows a new key once', async () => {
    session.setToken('t')
    vi.spyOn(globalThis, 'fetch').mockImplementation((input, init) => {
      const url = String(input)
      if (url.endsWith('/auth/me')) return Promise.resolve(json(ME))
      if (url.includes('/documents?source=api') || (url.includes('/documents?') && url.includes('source=api'))) {
        return Promise.resolve(json({ items: [{ id: 'd1', original_filename: 'scan-001.pdf', detected_mime_type: 'application/pdf', size_bytes: 1, status: 'COMPLETED', page_count: 1, received_at: '2026-10-01T10:00:00Z', source: 'api' }], next_cursor: null }))
      }
      if (url.endsWith('/api-keys') && init?.method === 'POST') {
        return Promise.resolve(json({ id: 'k1', name: 'scanner', prefix: 'ab12cd34', scopes: [], created_at: '', last_used_at: null, expires_at: null, revoked_at: null, token: 'idp_ab12cd34_secret' }, 201))
      }
      return Promise.resolve(json([]))
    })
    renderRoutes([{ path: '/', element: <InboxPage /> }], '/')
    expect(await screen.findByText('scan-001.pdf')).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('Name'), 'scanner')
    await userEvent.click(screen.getByRole('button', { name: 'Create key' }))
    expect(await screen.findByTestId('new-api-key')).toHaveTextContent('idp_ab12cd34_secret')
    expect(screen.getByText(/will not be shown again/)).toBeInTheDocument()
    session.clear()
  })
})
