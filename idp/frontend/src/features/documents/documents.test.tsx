import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { Me, Permission } from '@/features/auth/types'
import { DocumentDetailPage } from '@/features/documents/DocumentDetailPage'
import { DocumentsPage } from '@/features/documents/DocumentsPage'
import type { DocumentDetail, Page, Timeline } from '@/features/documents/types'
import { session } from '@/lib/session'
import { json, renderRoutes } from '@/test/render'

function me(permissions: Permission[]): Me {
  return {
    user: { id: 'u1', email: 'o@acme.test', full_name: 'O', role: 'owner', is_active: true, last_login_at: null, created_at: '' },
    tenant: { id: 't1', slug: 'acme', name: 'Acme' },
    permissions,
  }
}

const EMPTY_RESULT = { document_id: 'd1', status: 'FAILED', job_id: null, parts: [], pages: [], metrics: { steps: {}, duration_ms: 0, estimated_cost: 0 }, errors: [] }

const PAGE: Page = {
  page_number: 1,
  width: 612,
  height: 792,
  unit: 'pt',
  rotation: 0,
  has_text_layer: false,
  char_count: 0,
  text_source: null,
  ocr_status: null,
  text_quality: null,
  ocr_confidence: null,
  language: null,
  table_density: null,
  word_count: null,
  image_width: null,
  image_height: null,
}

const DOC: DocumentDetail = {
  id: 'd1',
  original_filename: 'invoice.pdf',
  detected_mime_type: 'application/pdf',
  declared_mime_type: 'application/pdf',
  size_bytes: 2048,
  status: 'FAILED',
  page_count: 2,
  received_at: '2026-10-03T12:00:00Z',
  sha256: 'a'.repeat(64),
  source: 'web_upload',
  scan_status: 'not_scanned',
  uploaded_by_id: 'u1',
  pages: [
    { ...PAGE, page_number: 1, has_text_layer: true, char_count: 80, text_source: 'native', ocr_status: 'not_needed', word_count: 12 },
    { ...PAGE, page_number: 2, text_source: 'none', ocr_status: 'not_configured' },
  ],
}

const TIMELINE: Timeline = {
  jobs: [
    {
      id: 'j1',
      workflow_key: 'ingest',
      workflow_version: 1,
      pipeline_version: '0.1.0',
      trigger: 'upload',
      status: 'DEAD_LETTERED',
      attempts: 3,
      max_attempts: 3,
      next_attempt_at: null,
      current_step: null,
      last_error_category: 'PROVIDER_ERROR',
      last_error_code: 'provider_error',
      last_error_message: 'Document probe timed out',
      correlation_id: 'cid-9',
      created_at: '2026-10-03T12:00:00Z',
      started_at: '2026-10-03T12:00:01Z',
      finished_at: '2026-10-03T12:05:00Z',
      steps: [
        {
          id: 's1',
          step_key: 'probe',
          attempt: 3,
          status: 'FAILED',
          provider: 'pdfium-pillow-probe',
          provider_version: 'x',
          metrics: {},
          error_category: 'PROVIDER_ERROR',
          error_code: 'provider_error',
          error_message: 'Document probe timed out',
          started_at: '2026-10-03T12:04:00Z',
          finished_at: '2026-10-03T12:05:00Z',
          duration_ms: 120000,
        },
      ],
    },
  ],
  status_changes: [
    { from_status: 'RECEIVED', to_status: 'QUEUED', reason: 'queued for processing', actor_type: 'user', actor_id: 'u1', occurred_at: '2026-10-03T12:00:00Z' },
    { from_status: 'PROCESSING', to_status: 'FAILED', reason: 'PROVIDER_ERROR: provider_error', actor_type: 'system', actor_id: null, occurred_at: '2026-10-03T12:05:00Z' },
  ],
}

function routeFetch(handlers: Record<string, () => Response>) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation((input, init) => {
    const url = String(input)
    const key = `${init?.method ?? 'GET'} ${url.split('?')[0]}`
    const handler = handlers[key]
    if (!handler) throw new Error(`unexpected request ${key}`)
    return Promise.resolve(handler())
  })
}

describe('documents', () => {
  it('links a duplicate upload to the existing document', async () => {
    session.setToken('t')
    routeFetch({
      'GET /api/v1/auth/me': () => json(me(['documents:read', 'documents:write'])),
      'GET /api/v1/documents': () => json({ items: [], next_cursor: null }),
      'POST /api/v1/documents': () =>
        json(
          {
            detail: 'This file was already uploaded to the project',
            error_category: 'BUSINESS_ERROR',
            code: 'duplicate_document',
            details: { existing_document_id: 'd-existing' },
          },
          409,
        ),
    })
    renderRoutes([{ path: '/documents', element: <DocumentsPage /> }], '/documents')

    expect(await screen.findByText('No documents yet.')).toBeInTheDocument()
    const file = new File(['%PDF-1.7'], 'invoice.pdf', { type: 'application/pdf' })
    await userEvent.upload(screen.getByLabelText(/Upload documents/), file)

    expect(await screen.findByText(/Already uploaded/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'open existing' })).toHaveAttribute('href', '/documents/d-existing')
    session.clear()
  })

  it('shows dead-lettered runs, OCR needs and offers replay to writers', async () => {
    session.setToken('t')
    routeFetch({
      'GET /api/v1/auth/me': () => json(me(['documents:read', 'documents:write'])),
      'GET /api/v1/documents/d1': () => json(DOC),
      'GET /api/v1/documents/d1/timeline': () => json(TIMELINE),
      'GET /api/v1/documents/d1/extraction': () => json(EMPTY_RESULT),
      'GET /api/v1/documents/d1/parts': () =>
        json([
          {
            id: 'p1', job_id: 'j1', part_index: 0, page_start: 1, page_end: 2,
            document_type_id: 't1', document_type_key: 'invoice', document_type_name: 'Invoice',
            schema_version_id: 'v1', schema_version: 3, classification_confidence: 0.92,
            classifier: 'rule-classifier@1', classification_reasons: ["page 1: keyword 'invoice'"], status: 'classified',
          },
        ]),
    })
    renderRoutes([{ path: '/documents/:id', element: <DocumentDetailPage /> }], '/documents/d1')

    expect(await screen.findByRole('heading', { name: 'invoice.pdf' })).toBeInTheDocument()
    expect(await screen.findByText('pages 1–2 · schema v3')).toBeInTheDocument()
    const run = await screen.findByRole('region', { name: 'Run j1' })
    expect(within(run).getByText('Dead-lettered')).toBeInTheDocument()
    expect(within(run).getByText(/attempt 3\/3/)).toBeInTheDocument()
    expect(within(run).getAllByText(/Document probe timed out/).length).toBeGreaterThan(0)
    expect(screen.getByText('image only · OCR not configured')).toBeInTheDocument()
    expect(screen.getByText('native · 12 words')).toBeInTheDocument()
    expect(screen.getByText('not scanned')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Replay/ })).toBeEnabled()
    session.clear()
  })

  it('hides write actions from read-only roles', async () => {
    session.setToken('t')
    routeFetch({
      'GET /api/v1/auth/me': () => json(me(['documents:read'])),
      'GET /api/v1/documents/d1': () => json({ ...DOC, status: 'COMPLETED' }),
      'GET /api/v1/documents/d1/timeline': () => json(TIMELINE),
      'GET /api/v1/documents/d1/extraction': () => json(EMPTY_RESULT),
      'GET /api/v1/documents/d1/parts': () => json([]),
    })
    renderRoutes([{ path: '/documents/:id', element: <DocumentDetailPage /> }], '/documents/d1')
    expect(await screen.findByRole('button', { name: /Download/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Reprocess|Replay/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Delete/ })).not.toBeInTheDocument()
    session.clear()
  })
})
