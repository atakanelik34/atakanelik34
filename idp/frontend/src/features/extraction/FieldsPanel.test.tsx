import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { FieldsPanel } from '@/features/extraction/FieldsPanel'
import type { FieldValue, PartResult } from '@/features/extraction/types'

function field(overrides: Partial<FieldValue>): FieldValue {
  return {
    id: 'f', path: 'total', row_id: '', value: '1249.50', original_value: '1249.50', raw_text: '1,249.50',
    confidence: 0.9, threshold: 0.85, below_threshold: false, required: true, type: 'decimal', status: 'extracted',
    normalized: true, method: 'key_value:right:native', provider: 'key-value-extractor',
    provenance: { page: 1, bbox: [0.7, 0.5, 0.8, 0.52], source_text: '1,249.50' }, alternatives: [],
    ...overrides,
  }
}

const PART: PartResult = {
  part_id: 'p1',
  pages: [1, 1],
  classification: { document_type: 'invoice', document_type_name: 'Invoice', confidence: 1, classifier: 'rule', reasons: [] },
  schema_version: 1,
  extraction: { route: 'DETERMINISTIC', providers: [], route_trace: {}, metrics: {} },
  fields: {
    total: field({}),
    vendor_name: field({ id: 'v', path: 'vendor_name', value: 'ACME', confidence: 0.45, threshold: 0.8, below_threshold: true, required: true }),
    due_date: field({ id: 'd', path: 'due_date', value: null, confidence: 0, status: 'missing', required: false }),
  },
  tables: {
    'lines[]': [{ row_id: 'r_1', cells: { description: field({ id: 'c1', path: 'lines[].description', row_id: 'r_1', value: 'Pump' }), total: field({ id: 'c2', path: 'lines[].total', row_id: 'r_1', value: '900.00' }) } }],
  },
  validation: [],
  enrichment: [],
  actions: [],
}

describe('FieldsPanel', () => {
  it('shows confidence against each field threshold, missing values and line items', async () => {
    const onSelect = vi.fn<(f: FieldValue) => void>()
    render(<FieldsPanel part={PART} selectedId={null} onSelect={onSelect} />)
    expect(screen.getByText('90%')).toBeInTheDocument()
    expect(screen.getByText('45%')).toHaveAttribute('title', 'threshold 80%')
    expect(screen.getByText('missing')).toBeInTheDocument()
    expect(screen.getByText('lines · 1 rows')).toBeInTheDocument()

    await userEvent.click(screen.getByText('900.00'))
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ id: 'c2', row_id: 'r_1' }))
    await userEvent.click(screen.getByText('1249.50'))
    expect(onSelect).toHaveBeenLastCalledWith(expect.objectContaining({ path: 'total' }))
  })
})
