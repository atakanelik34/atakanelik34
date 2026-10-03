import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { ConnectionDetailPage } from '@/features/enrichment/ConnectionDetailPage'
import { EnrichmentPanel } from '@/features/enrichment/EnrichmentPanel'
import { session } from '@/lib/session'
import { json, renderRoutes } from '@/test/render'

const ME = {
  user: { id: 'u', email: 'a@b.c', full_name: 'A', role: 'admin', is_active: true, last_login_at: null, created_at: '' },
  tenant: { id: 't', slug: 'acme', name: 'Acme' },
  permissions: ['config:read', 'config:write'],
}

describe('connection detail', () => {
  it('imports a CSV, lists records and runs a test lookup', async () => {
    session.setToken('t')
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation((input, init) => {
      const url = String(input)
      if (url.endsWith('/auth/me')) return Promise.resolve(json(ME))
      if (url.includes('/records/import')) return Promise.resolve(json({ imported: 2, skipped: 1, errors: ['line 4: duplicate key'] }))
      if (url.includes('/records')) {
        return Promise.resolve(json({ total: 1, items: [{ key: 'V-1', entity: 'vendor', name: 'ACME GmbH', attributes: { tax_id: 'DE1' } }] }))
      }
      if (url.endsWith('/test') && init?.method === 'POST') {
        return Promise.resolve(json({ status: 'matched', best: null, candidates: [{ key: 'V-1', name: 'ACME GmbH', attributes: {}, score: 1, matched_on: ['tax_id'] }] }))
      }
      return Promise.resolve(json({ id: 'c1', key: 'vendors', name: 'Vendor master', kind: 'master_data', config: {}, is_active: true, is_mock: false, records: 1, created_at: '' }))
    })
    renderRoutes([{ path: '/connections/:id', element: <ConnectionDetailPage /> }], '/connections/c1')
    expect(await screen.findByText('ACME GmbH')).toBeInTheDocument()

    const file = new File(['key,name\nV-1,ACME GmbH\n'], 'vendors.csv', { type: 'text/csv' })
    await userEvent.upload(screen.getByLabelText('Choose CSV file'), file)
    expect(await screen.findByText(/2 imported, 1 skipped/)).toBeInTheDocument()

    await userEvent.type(screen.getByLabelText('Tax id'), 'DE1')
    await userEvent.click(screen.getByRole('button', { name: 'Look up' }))
    expect(await screen.findByText('matched')).toBeInTheDocument()
    const test = fetchSpy.mock.calls.find(([u]) => String(u).endsWith('/test'))
    expect(JSON.parse(test?.[1]?.body as string)).toEqual({ criteria: { tax_id: 'DE1' } })
    session.clear()
  })
})

describe('enrichment panel', () => {
  it('shows matched outputs, mock labels and honest not-configured states', () => {
    renderRoutes(
      [
        {
          path: '/',
          element: (
            <EnrichmentPanel
              outcomes={[
                { name: 'vendor', connection: 'vendors', provider: 'mock_erp', status: 'matched', record_key: 'MOCK-V-1001', score: 1, matched_on: ['tax_id'], criteria: {}, outputs: { key: 'MOCK-V-1001', payment_terms: 'NET30' }, candidates: [], is_mock: true, message: '' },
                { name: 'customer', connection: 'crm', provider: '', status: 'not_configured', record_key: null, score: null, matched_on: [], criteria: {}, outputs: {}, candidates: [], is_mock: false, message: 'connection not configured' },
              ]}
            />
          ),
        },
      ],
      '/',
    )
    expect(screen.getByText('mock')).toBeInTheDocument()
    expect(screen.getByText('NET30')).toBeInTheDocument()
    expect(screen.getByText('not configured')).toBeInTheDocument()
    expect(screen.getByText('connection not configured')).toBeInTheDocument()
  })
})
