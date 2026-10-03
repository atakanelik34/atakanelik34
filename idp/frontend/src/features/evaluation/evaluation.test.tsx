import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { DatasetPage } from '@/features/evaluation/DatasetPage'
import { ProvidersPage } from '@/features/processing/ProvidersPage'
import { RouteTraceView } from '@/features/routing/RouteTrace'
import type { RouteTrace } from '@/features/extraction/types'
import { session } from '@/lib/session'
import { json, renderRoutes } from '@/test/render'

const ME = {
  user: { id: 'u', email: 'a@b.c', full_name: 'A', role: 'admin', is_active: true, last_login_at: null, created_at: '' },
  tenant: { id: 't', slug: 'acme', name: 'Acme' },
  permissions: ['config:read', 'config:write'],
}

const METRICS = {
  compared: 10, tp: 8, fp: 2, fn: 2, precision: 0.8, recall: 0.8, f1: 0.8, exact_match: 0.7, normalized_match: 0.8,
  mean_confidence: 0.85, mean_confidence_correct: 0.9, mean_confidence_incorrect: 0.6, overconfident: 1,
}

const RUN = {
  id: 'r1', status: 'completed', created_at: '2026-10-01T10:00:00Z', finished_at: null, dataset_id: 'ds1',
  metrics: { ...METRICS, items: 1, items_without_result: 0, intervention_rate: 1, cost_per_document: 0, latency_ms_per_document: 420 },
  config: { route: ['NATIVE_TEXT'] },
  field_metrics: { invoice_number: { ...METRICS, f1: 0 }, total: { ...METRICS, f1: 1 } },
  item_results: [],
}

describe('evaluation dataset page', () => {
  it('shows the latest run, per-field metrics, and runs an evaluation', async () => {
    session.setToken('t')
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation((input, init) => {
      const url = String(input)
      if (url.endsWith('/auth/me')) return Promise.resolve(json(ME))
      if (url.endsWith('/datasets/ds1')) {
        return Promise.resolve(json({ id: 'ds1', name: 'golden', description: '', document_type: 'invoice', items: 1, created_at: '', last_run: null }))
      }
      if (url.endsWith('/datasets/ds1/runs') && init?.method === 'POST') return Promise.resolve(json(RUN, 201))
      if (url.endsWith('/datasets/ds1/runs')) return Promise.resolve(json([RUN]))
      if (url.endsWith('/runs/r1')) return Promise.resolve(json(RUN))
      return Promise.resolve(json({ detail: 'nope', error_category: 'NOT_FOUND' }, 404))
    })
    renderRoutes([{ path: '/evaluation/:id', element: <DatasetPage /> }], '/evaluation/ds1')

    expect(await screen.findByText('Per field')).toBeInTheDocument()
    const row = screen.getByText('invoice_number').closest('tr')
    expect(row).not.toBeNull()
    expect(within(row as HTMLElement).getByText('0.0%')).toBeInTheDocument()
    expect(screen.getByText('Intervention rate').nextSibling).toHaveTextContent('100.0%')

    await userEvent.click(screen.getByRole('button', { name: 'Run evaluation' }))
    expect(fetchSpy.mock.calls.some(([u, i]) => String(u).endsWith('/datasets/ds1/runs') && i?.method === 'POST')).toBe(true)
    session.clear()
  })
})

describe('providers page', () => {
  it('states the effective policy, marks unconfigured providers, and saves a policy', async () => {
    session.setToken('t')
    const policy = { mode: 'LOCAL_ONLY', allow_llm: true, allow_mock_providers: false, max_cost_per_document: null, version: 0, routing_version: 1 }
    let requested: Record<string, unknown> = policy
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation((input, init) => {
      const url = String(input)
      if (url.endsWith('/auth/me')) return Promise.resolve(json({ ...ME, permissions: [...ME.permissions, 'tenant:manage'] }))
      if (url.endsWith('/processing-policy')) {
        if (init?.method === 'PUT') requested = { ...policy, ...JSON.parse(init.body as string), version: 1 }
        return Promise.resolve(json({ requested, effective: { ...requested, mode: 'LOCAL_ONLY' }, ceiling: policy }))
      }
      if (url.includes('/providers/usage')) {
        return Promise.resolve(json([{ provider: 'ollama', model: 'llama3.1:8b', locality: 'local', purpose: 'extraction', status: 'ok', calls: 3, input_tokens: 1200, output_tokens: 90, cost: 0 }]))
      }
      return Promise.resolve(
        json({
          policy,
          providers: [
            { kind: 'llm', name: 'anthropic', version: '', method: 'llm', tier: null, locality: 'cloud', is_mock: false, status: 'not_configured', cost_per_page: 0 },
            { kind: 'extraction', name: 'regex-extractor', version: '1', method: 'regex', tier: 0, locality: 'local', is_mock: false, status: 'configured', cost_per_page: 0 },
          ],
        }),
      )
    })
    renderRoutes([{ path: '/', element: <ProvidersPage /> }], '/')
    expect(await screen.findByText(/never leaves this deployment/)).toBeInTheDocument()
    expect(screen.getByText('Provider not configured')).toBeInTheDocument()
    expect(screen.getByText('deterministic')).toBeInTheDocument()
    expect(await screen.findByText('1,200 / 90')).toBeInTheDocument()

    await userEvent.selectOptions(screen.getByLabelText('Mode'), 'CLOUD_ALLOWED')
    await userEvent.click(screen.getByRole('button', { name: 'Save policy' }))
    const put = fetchSpy.mock.calls.find(([, i]) => i?.method === 'PUT')
    expect(JSON.parse(put?.[1]?.body as string)).toMatchObject({ mode: 'CLOUD_ALLOWED', allow_llm: true })
    expect(await screen.findByText(/Limited by the deployment/)).toBeInTheDocument()
    session.clear()
  })
})

describe('route trace', () => {
  it('explains stages, failures and rejected providers', () => {
    const trace: RouteTrace = {
      route: 'NATIVE_TEXT', routing_version: 1, policy_version: 0, signals: {}, estimated_cost: 0,
      reasons: ['native text layer on all pages'], planned_stages: [['regex-extractor'], ['local-llm']],
      rejected: [{ provider: 'cloud-llm', reason: 'policy LOCAL_ONLY forbids cloud providers' }],
      stages: [
        { stage: 0, outcome: 'escalated', unresolved: ['vendor_name'], attempts: [{ provider: 'regex-extractor', status: 'ok', candidates: 4, duration_ms: 3 }] },
        { stage: 1, outcome: 'exhausted', unresolved: ['vendor_name'], attempts: [{ provider: 'local-llm', status: 'failed', error: 'TimeoutError' }] },
      ],
    }
    renderRoutes([{ path: '/', element: <RouteTraceView trace={trace} /> }], '/')
    expect(screen.getByText('NATIVE TEXT')).toBeInTheDocument()
    expect(screen.getByText('escalated')).toBeInTheDocument()
    expect(screen.getByText('TimeoutError')).toBeInTheDocument()
    expect(screen.getByText(/forbids cloud providers/)).toBeInTheDocument()
  })
})
