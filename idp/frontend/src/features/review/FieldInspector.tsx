import { Check, Pencil, X } from 'lucide-react'
import { useState, type FormEvent } from 'react'

import { Button } from '@/components/ui/button'
import { ErrorNotice, Spinner } from '@/components/ui/feedback'
import { Input } from '@/components/ui/input'
import { ConfidenceBadge } from '@/features/extraction/ConfidenceBadge'
import { displayValue, type FieldValue } from '@/features/extraction/types'
import { useFieldAction } from '@/features/review/api'

/** The selected field: value, evidence, alternatives, and reviewer decisions. */
export function FieldInspector({
  taskId,
  field,
  editable,
}: {
  taskId: string
  field: FieldValue
  editable: boolean
}) {
  const act = useFieldAction(taskId)
  const [editing, setEditing] = useState(field.status === 'missing')
  const [value, setValue] = useState(field.value === null || field.value === undefined ? '' : String(field.value))
  const [reason, setReason] = useState('')

  function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    act.mutate(
      { fieldId: field.id, action: 'edit', value, reason: reason || undefined },
      { onSuccess: () => setEditing(false) },
    )
  }

  return (
    <div className="space-y-3 px-4 py-3">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-[11px] text-muted">
            {field.path} {field.type ? `· ${field.type}` : ''}
          </p>
          <p className="text-sm font-semibold break-all">{displayValue(field.value)}</p>
        </div>
        <ConfidenceBadge field={field} />
      </div>
      <dl className="grid grid-cols-3 gap-x-2 gap-y-1 text-[11px]">
        <dt className="text-muted">Source</dt>
        <dd className="col-span-2 font-mono break-all">{field.provenance.source_text ?? '—'}</dd>
        <dt className="text-muted">Page</dt>
        <dd className="col-span-2">{field.provenance.page ?? '—'}</dd>
        <dt className="text-muted">Method</dt>
        <dd className="col-span-2 font-mono">{field.method ?? '—'}</dd>
        <dt className="text-muted">Threshold</dt>
        <dd className="col-span-2">{Math.round(field.threshold * 100)}%</dd>
      </dl>
      {field.alternatives.length > 0 && editable ? (
        <div>
          <p className="mb-1 text-[11px] text-muted">Other candidates</p>
          <div className="flex flex-wrap gap-1">
            {field.alternatives.map((alt) => (
              <button
                key={String(alt.value)}
                type="button"
                className="rounded border border-border px-2 py-0.5 text-xs hover:border-accent"
                onClick={() => act.mutate({ fieldId: field.id, action: 'edit', value: alt.value, reason: 'picked alternative' })}
              >
                {displayValue(alt.value)} · {Math.round(alt.confidence * 100)}%
              </button>
            ))}
          </div>
        </div>
      ) : null}
      {act.isError ? <ErrorNotice error={act.error} /> : null}
      {editable ? (
        editing ? (
          <form onSubmit={save} className="space-y-2">
            <Input aria-label="Corrected value" value={value} onChange={(e) => setValue(e.target.value)} />
            <Input aria-label="Reason (optional)" placeholder="Reason (optional)" value={reason} onChange={(e) => setReason(e.target.value)} />
            <div className="flex gap-2">
              <Button type="submit" size="sm" disabled={act.isPending}>
                {act.isPending ? <Spinner /> : null}
                Save
              </Button>
              {field.status !== 'missing' ? (
                <Button type="button" size="sm" variant="ghost" onClick={() => setEditing(false)}>
                  Cancel
                </Button>
              ) : null}
            </div>
          </form>
        ) : (
          <div className="flex gap-2">
            <Button size="sm" variant="secondary" disabled={act.isPending} onClick={() => act.mutate({ fieldId: field.id, action: 'accept' })}>
              <Check />
              Accept
            </Button>
            <Button size="sm" variant="secondary" onClick={() => setEditing(true)}>
              <Pencil />
              Edit
            </Button>
            <Button size="sm" variant="ghost" disabled={act.isPending} onClick={() => act.mutate({ fieldId: field.id, action: 'reject' })}>
              <X />
              Reject
            </Button>
          </div>
        )
      ) : null}
    </div>
  )
}
