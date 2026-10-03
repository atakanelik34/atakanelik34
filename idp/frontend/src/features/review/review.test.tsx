import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { AuditLogPage } from '@/features/audit/AuditLogPage'
import type { FieldValue } from '@/features/extraction/types'
import { ReviewWorkspacePage } from '@/features/review/ReviewWorkspacePage'
import { session } from '@/lib/session'
import { json, renderRoutes } from '@/test/render'

const ME = {
  user: { id: 'u', email: 'a@b.c', full_name: 'A', role: 'reviewer', is_active: true, last_login_at: null, created_at: '' },
  tenant: { id: 't', slug: 'acme', name: 'Acme' },
  permissions: ['reviews:read', 'reviews:write', 'documents:read', 'audit:read'],
}

function field(overrides: Partial<FieldValue>): FieldValue {
  return {
    id: 'f', path: 'total', row_id: '', value: '1249.50', original_value: '1249.50', raw_text: '1,249.50',
    confidence: 0.9, threshold: 0.85, below_threshold: false, required: true, type: 'decimal', status: 'extracted',
    normalized: true, method: 'key_value:right:native', provider: 'key-value-extractor',
    provenance: { page: 1, bbox: [0.7, 0.5, 0.8, 0.52], source_text: 'Total 1,249.50' }, alternatives: [],
    ...overrides,
  }
}

const DETAIL = {
  task: {
    id: 'rt1', document_id: 'd1', document_name: 'inv.pdf', job_id: 'j1', status: 'in_progress', assignee_id: 'u',
    reasons: [{ part_id: 'p1', rule_id: 'sum', rule_type: 'sum', outcome: 'FAIL', fields: ['total'], message: 'Lines do not add up' }],
    created_at: '', resolved_at: null, resolution_note: null,
  },
  document: { id: 'd1', original_filename: 'inv.pdf', page_count: 1, status: 'WAITING_FOR_HUMAN' },
  parts: [
    {
      part_id: 'p1', pages: [1, 1], document_type: 'invoice', document_type_name: 'Invoice', classification_confidence: 1, schema_version: 1,
      fields: {
        total: field({}),
        vendor_name: field({ id: 'v', path: 'vendor_name', value: 'ACME', confidence: 0.4, below_threshold: true, alternatives: [{ value: 'ACME GmbH', confidence: 0.35, method: 'first_line', page: 1, bbox: null }] }),
      },
      tables: {},
      validation: [{ rule_id: 'sum', rule_type: 'sum', outcome: 'FAIL', fields: ['total'], message: 'Lines do not add up', severity: 'FAIL', details: {} }],
    },
  ],
  actions: [],
}

describe('review workspace', () => {
  it('shows evidence for the selected field, saves a correction and approves with overrides', async () => {
    session.setToken('t')
    Element.prototype.scrollIntoView = vi.fn<() => void>()
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
      const url = String(input)
      if (url.endsWith('/auth/me')) return Promise.resolve(json(ME))
      if (url.endsWith('/reviews/rt1')) return Promise.resolve(json(DETAIL))
      if (url.includes('/reviews/rt1/')) return Promise.resolve(json({ ok: true }))
      return Promise.resolve(json({ detail: 'not found', error_category: 'NOT_FOUND' }, 404))
    })
    const { router } = renderRoutes(
      [
        { path: '/reviews/:id', element: <ReviewWorkspacePage /> },
        { path: '/reviews', element: <p>queue</p> },
      ],
      '/reviews/rt1',
    )

    await userEvent.click(await screen.findByText('ACME'))
    const inspector = screen.getByRole('complementary', { name: 'Review' })
    expect(within(inspector).getByText('vendor_name · decimal')).toBeInTheDocument()
    expect(within(inspector).getByText('Total 1,249.50')).toBeInTheDocument()

    await userEvent.click(within(inspector).getByRole('button', { name: 'Edit' }))
    const input = within(inspector).getByLabelText('Corrected value')
    await userEvent.clear(input)
    await userEvent.type(input, 'ACME Industrial GmbH')
    await userEvent.click(within(inspector).getByRole('button', { name: 'Save' }))
    const edit = fetchSpy.mock.calls.find(([u]) => String(u).endsWith('/reviews/rt1/fields/v'))
    expect(JSON.parse(edit?.[1]?.body as string)).toEqual({ action: 'edit', value: 'ACME Industrial GmbH' })

    expect(screen.getByText(/1 check\(s\) still need attention/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Approve' }))
    await userEvent.click(screen.getByRole('button', { name: 'Confirm' }))
    expect(fetchSpy.mock.calls.some(([u, init]) => String(u).endsWith('/reviews/rt1/approve') && init?.method === 'POST')).toBe(true)
    await vi.waitFor(() => expect(router.state.location.pathname).toBe('/reviews'))
    session.clear()
  })

  it('requires a reason before rejecting', async () => {
    session.setToken('t')
    vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
      const url = String(input)
      if (url.endsWith('/auth/me')) return Promise.resolve(json(ME))
      if (url.endsWith('/reviews/rt1')) return Promise.resolve(json(DETAIL))
      return Promise.resolve(json({ detail: 'not found', error_category: 'NOT_FOUND' }, 404))
    })
    renderRoutes([{ path: '/reviews/:id', element: <ReviewWorkspacePage /> }], '/reviews/rt1')
    await userEvent.click(await screen.findByRole('button', { name: 'Reject' }))
    const confirm = screen.getByRole('button', { name: 'Confirm' })
    expect(confirm).toBeDisabled()
    await userEvent.type(screen.getByLabelText('Reason'), 'duplicate invoice')
    expect(confirm).toBeEnabled()
    session.clear()
  })
})

describe('audit log', () => {
  it('lists entries with a status-change summary', async () => {
    session.setToken('t')
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
      const url = String(input)
      if (url.endsWith('/auth/me')) return Promise.resolve(json(ME))
      return Promise.resolve(
        json({
          items: [
            {
              id: 'a1', occurred_at: '2026-10-01T10:00:00Z', actor_type: 'user', actor_id: 'u', actor_email: 'a@b.c',
              action: 'review.approved', entity_type: 'review_task', entity_id: 'rt1',
              before: { status: 'in_progress' }, after: { status: 'approved' }, correlation_id: 'c1', ip_address: '10.0.0.1',
            },
          ],
          next_cursor: null,
        }),
      )
    })
    renderRoutes([{ path: '/audit', element: <AuditLogPage /> }], '/audit')
    expect(await screen.findByText('review.approved')).toBeInTheDocument()
    expect(screen.getByText('in_progress → approved')).toBeInTheDocument()
    session.clear()
    expect(fetchSpy.mock.calls.some(([u]) => String(u).includes('/audit-logs?limit=50'))).toBe(true)
  })
})
