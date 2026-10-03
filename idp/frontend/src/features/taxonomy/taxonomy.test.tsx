import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { DocumentTypeDetailPage } from '@/features/taxonomy/DocumentTypeDetailPage'
import { session } from '@/lib/session'
import { json, renderRoutes } from '@/test/render'

const ME = {
  user: { id: 'u', email: 'a@b.c', full_name: 'A', role: 'admin', is_active: true, last_login_at: null, created_at: '' },
  tenant: { id: 't', slug: 'acme', name: 'Acme' },
  permissions: ['config:read', 'config:write'],
}

const DEFINITION = {
  fields: [
    { name: 'total', type: 'decimal', required: true, description: '', aliases: ['total due'], extraction_hints: { patterns: [], position: 'any' }, validation_rules: [{ type: 'min', params: {}, severity: 'FAIL', message: null }], confidence_threshold: 0.85, normalization: {}, children: [], item: null },
  ],
  rules: [],
  classification: { keywords: ['invoice'], negative_keywords: [], first_page_markers: [], min_score: 0.5 },
}

describe('document type editor', () => {
  it('shows field preview and surfaces server validation errors on save', async () => {
    session.setToken('t')
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation((input, init) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url.endsWith('/auth/me')) return Promise.resolve(json(ME))
      if (method === 'PUT') {
        return Promise.resolve(
          json({ detail: 'Schema definition is invalid', error_category: 'VALIDATION_ERROR', details: { errors: [{ loc: ['fields', '0', 'type'], msg: 'Input should be a valid type' }] } }, 422),
        )
      }
      if (url.includes('/schemas/2')) {
        return Promise.resolve(json({ id: 'v2', version: 2, status: 'draft', definition: DEFINITION, created_at: '', published_at: null }))
      }
      return Promise.resolve(
        json({
          id: 'dt1', key: 'invoice', name: 'Invoice', description: '', is_active: true, project_id: null, created_at: '',
          versions: [
            { id: 'v2', version: 2, status: 'draft', created_at: '2026-10-01T00:00:00Z', published_at: null },
            { id: 'v1', version: 1, status: 'published', created_at: '2026-09-01T00:00:00Z', published_at: '2026-09-01T00:00:00Z' },
          ],
        }),
      )
    })
    renderRoutes([{ path: '/document-types/:id', element: <DocumentTypeDetailPage /> }], '/document-types/dt1')

    expect(await screen.findByText('total')).toBeInTheDocument()
    expect(screen.getByText('85%')).toBeInTheDocument()
    expect(screen.getByText('1 paths · 0 cross-field rules · 1 classification keywords')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Save draft' }))
    expect(await screen.findByText('fields.0.type: Input should be a valid type')).toBeInTheDocument()
    const put = fetchSpy.mock.calls.find(([, init]) => init?.method === 'PUT')
    expect(JSON.parse(put?.[1]?.body as string)).toEqual({ definition: DEFINITION })
    session.clear()
  })
})
