import { ArrowLeft, Plus, Trash2 } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardHeader, CardTitle } from '@/components/ui/card'
import { ErrorNotice, Skeleton } from '@/components/ui/feedback'
import { hasPermission, useMe } from '@/features/auth/api'
import { FieldsPanel } from '@/features/extraction/FieldsPanel'
import type { FieldValue, PartResult } from '@/features/extraction/types'
import { useAddRow, useClaim, useDeleteRow, useReview, type ReviewPart } from '@/features/review/api'
import { DecisionPanel } from '@/features/review/DecisionPanel'
import { FieldInspector } from '@/features/review/FieldInspector'
import { ValidationPanel } from '@/features/review/ValidationPanel'
import { DocumentViewer, type Highlight } from '@/features/viewer/DocumentViewer'

function asPartResult(part: ReviewPart): PartResult {
  return {
    part_id: part.part_id,
    pages: part.pages,
    classification: {
      document_type: part.document_type,
      document_type_name: part.document_type_name,
      confidence: part.classification_confidence,
      classifier: '',
      reasons: [],
    },
    schema_version: part.schema_version,
    extraction: null,
    // Rows deleted in review stay in history but are not shown as live data.
    fields: part.fields,
    tables: Object.fromEntries(
      Object.entries(part.tables).map(([k, rows]) => [
        k,
        rows.filter((r) => !Object.values(r.cells).every((c) => c.status === 'rejected')),
      ]),
    ),
    validation: part.validation,
    enrichment: [],
    actions: [],
  }
}

function allFields(part: ReviewPart): FieldValue[] {
  return [...Object.values(part.fields), ...Object.values(part.tables).flatMap((rows) => rows.flatMap((r) => Object.values(r.cells)))]
}

export function ReviewWorkspacePage() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const { data: me } = useMe()
  const review = useReview(id)
  const claim = useClaim(id)
  const addRow = useAddRow(id)
  const deleteRow = useDeleteRow(id)
  const [page, setPage] = useState(1)
  const [selectedId, setSelectedId] = useState<string | null>(null)

  const data = review.data
  const fields = useMemo(() => (data ? data.parts.flatMap(allFields) : []), [data])
  const selected = fields.find((f) => f.id === selectedId) ?? null
  const isOpen = data ? ['open', 'in_progress'].includes(data.task.status) : false
  const editable = isOpen && hasPermission(me, 'reviews:write')
  const blocking = data ? data.parts.flatMap((p) => p.validation).filter((v) => v.outcome === 'FAIL' || v.outcome === 'REQUIRES_HUMAN').length : 0

  const highlights: Highlight[] =
    selected?.provenance.bbox && selected.provenance.page
      ? [{ page: selected.provenance.page, bbox: selected.provenance.bbox, label: selected.path }]
      : []

  function select(field: FieldValue) {
    setSelectedId(field.id)
    if (field.provenance.page) setPage(field.provenance.page)
  }

  function selectPath(path: string) {
    const field = fields.find((f) => f.path === path && !f.row_id)
    if (field) select(field)
  }

  if (review.isError) return <ErrorNotice error={review.error} title="Unable to load review" />
  if (!data) return <Skeleton className="h-[80vh]" />

  return (
    <div className="-mx-6 -my-6 flex h-[calc(100vh-3.5rem)] flex-col">
      <div className="flex items-center gap-3 border-b border-border bg-surface px-6 py-3">
        <Link to="/reviews" className="text-muted hover:text-ink" aria-label="Back to review queue">
          <ArrowLeft className="size-4" />
        </Link>
        <h1 className="truncate text-sm font-semibold">{data.document.original_filename}</h1>
        <Badge tone={isOpen ? 'warning' : 'neutral'}>{data.task.status.replace('_', ' ')}</Badge>
        <Link to={`/documents/${data.document.id}`} className="text-xs text-accent hover:underline">
          document
        </Link>
        {editable && data.task.status === 'open' ? (
          <Button size="sm" variant="secondary" className="ml-auto" onClick={() => claim.mutate(undefined)}>
            Start review
          </Button>
        ) : null}
      </div>
      <div className="grid min-h-0 flex-1 grid-cols-12">
        <section aria-label="Document" className="col-span-5 min-h-0 border-r border-border bg-surface">
          <DocumentViewer
            className="h-full"
            documentId={data.document.id}
            pageCount={data.document.page_count ?? 1}
            page={page}
            onPageChange={setPage}
            highlights={highlights}
          />
        </section>
        <section aria-label="Extracted data" className="col-span-4 min-h-0 overflow-y-auto border-r border-border bg-surface">
          {data.parts.map((part) => (
            <div key={part.part_id} className="border-b border-border">
              <div className="flex items-center justify-between bg-canvas/60 px-4 py-2">
                <p className="text-xs font-semibold">
                  {part.document_type_name ?? 'Unclassified'} · pages {part.pages[0]}–{part.pages[1]}
                </p>
                {editable && Object.keys(part.tables).length > 0 ? (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() =>
                      addRow.mutate({ part_id: part.part_id, array_path: Object.keys(part.tables)[0] ?? 'lines[]', cells: { description: 'New line' } })
                    }
                  >
                    <Plus />
                    Row
                  </Button>
                ) : null}
              </div>
              {Object.keys(part.fields).length === 0 ? (
                <p className="px-4 py-3 text-xs text-muted">No fields — the document type could not be determined.</p>
              ) : (
                <FieldsPanel
                  part={asPartResult(part)}
                  selectedId={selectedId}
                  onSelect={select}
                  renderActions={(field) =>
                    editable && field.row_id ? (
                      <button
                        type="button"
                        aria-label="Delete row"
                        className="text-muted hover:text-danger"
                        onClick={() => deleteRow.mutate({ part_id: part.part_id, row_id: field.row_id })}
                      >
                        <Trash2 className="size-3.5" />
                      </button>
                    ) : null
                  }
                />
              )}
            </div>
          ))}
        </section>
        <aside aria-label="Review" className="col-span-3 min-h-0 space-y-4 overflow-y-auto bg-canvas p-4">
          <Card>
            <CardHeader>
              <CardTitle>{selected ? 'Field' : 'Select a field'}</CardTitle>
            </CardHeader>
            {selected ? (
              <FieldInspector key={`${selected.id}:${String(selected.value)}:${selected.status}`} taskId={id} field={selected} editable={editable} />
            ) : (
              <p className="px-4 py-3 text-xs text-muted">Click a value to see where it came from.</p>
            )}
          </Card>
          {data.parts.map((part) => (
            <Card key={part.part_id}>
              <CardHeader>
                <CardTitle>Validation</CardTitle>
              </CardHeader>
              <ValidationPanel part={part} onSelectPath={selectPath} />
            </Card>
          ))}
          {editable ? (
            <Card>
              <CardHeader>
                <CardTitle>Decision</CardTitle>
              </CardHeader>
              <DecisionPanel taskId={id} blocking={blocking} onDone={() => void navigate('/reviews')} />
            </Card>
          ) : null}
          <Card>
            <CardHeader>
              <CardTitle>History</CardTitle>
            </CardHeader>
            <ul className="divide-y divide-border text-xs">
              {data.actions.length === 0 ? <li className="px-4 py-2 text-muted">No actions yet.</li> : null}
              {data.actions.map((a) => (
                <li key={a.id} className="px-4 py-2">
                  <span className="font-medium">{a.action}</span>
                  {a.path ? <span className="text-muted"> · {a.path}</span> : null}
                  {a.reason ? <p className="text-muted">{a.reason}</p> : null}
                </li>
              ))}
            </ul>
          </Card>
        </aside>
      </div>
    </div>
  )
}
