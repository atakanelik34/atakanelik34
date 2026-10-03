import type { ReactNode } from 'react'

import { ConfidenceBadge } from '@/features/extraction/ConfidenceBadge'
import { displayValue, type FieldValue, type PartResult } from '@/features/extraction/types'
import { cn } from '@/lib/utils'

export interface FieldSelection {
  fieldId: string
}

function Row({
  field,
  label,
  selected,
  onSelect,
  renderActions,
}: {
  field: FieldValue
  label: string
  selected: boolean
  onSelect: (field: FieldValue) => void
  renderActions?: (field: FieldValue) => ReactNode
}) {
  return (
    <li
      className={cn(
        'group flex items-start gap-3 px-4 py-2',
        selected ? 'bg-accent-soft' : field.below_threshold ? 'bg-warning-soft/40' : '',
      )}
    >
      <button type="button" className="min-w-0 flex-1 text-left" onClick={() => onSelect(field)}>
        <p className="text-[11px] text-muted">
          {label}
          {field.required ? <span className="text-danger"> *</span> : null}
        </p>
        <p className={cn('truncate text-sm', field.status === 'missing' ? 'text-muted italic' : 'font-medium')}>
          {displayValue(field.value)}
        </p>
        {field.status === 'corrected' ? (
          <p className="truncate text-[11px] text-muted line-through">{displayValue(field.original_value)}</p>
        ) : null}
        {!field.normalized && field.provenance.normalization_error ? (
          <p className="text-[11px] text-warning">{field.provenance.normalization_error}</p>
        ) : null}
      </button>
      <div className="flex shrink-0 items-center gap-1.5 pt-1">
        <ConfidenceBadge field={field} />
        {renderActions?.(field)}
      </div>
    </li>
  )
}

export function FieldsPanel({
  part,
  selectedId,
  onSelect,
  renderActions,
}: {
  part: PartResult
  selectedId: string | null
  onSelect: (field: FieldValue) => void
  renderActions?: (field: FieldValue) => ReactNode
}) {
  const scalars = Object.values(part.fields)
  return (
    <div className="divide-y divide-border">
      <ul className="divide-y divide-border">
        {scalars.map((field) => (
          <Row
            key={field.id}
            field={field}
            label={field.path.replaceAll('_', ' ')}
            selected={selectedId === field.id}
            onSelect={onSelect}
            renderActions={renderActions}
          />
        ))}
      </ul>
      {Object.entries(part.tables).map(([path, rows]) => {
        const columns = Array.from(new Set(rows.flatMap((r) => Object.keys(r.cells))))
        return (
          <div key={path} className="px-4 py-3">
            <p className="mb-2 text-[11px] font-semibold tracking-wide text-muted uppercase">
              {path.replace('[]', '')} · {rows.length} rows
            </p>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-left text-muted">
                    {columns.map((c) => (
                      <th key={c} className="py-1 pr-3 font-medium">
                        {c.replaceAll('_', ' ')}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.row_id} className="border-t border-border">
                      {columns.map((c) => {
                        const cell = row.cells[c]
                        return (
                          <td key={c} className="py-1 pr-3">
                            {cell ? (
                              <button
                                type="button"
                                onClick={() => onSelect(cell)}
                                className={cn(
                                  'rounded px-1 text-left hover:bg-accent-soft',
                                  selectedId === cell.id && 'bg-accent-soft ring-1 ring-accent',
                                  cell.below_threshold && 'text-warning',
                                )}
                                title={`${Math.round(cell.confidence * 100)}%`}
                              >
                                {displayValue(cell.value)}
                              </button>
                            ) : (
                              <span className="text-muted">—</span>
                            )}
                          </td>
                        )
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )
      })}
    </div>
  )
}
