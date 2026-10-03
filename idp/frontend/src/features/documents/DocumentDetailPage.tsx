import { ArrowLeft, Download, RotateCw, ShieldAlert, Trash2 } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { Link, useNavigate, useParams } from 'react-router'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { ErrorNotice, Skeleton } from '@/components/ui/feedback'
import { Table, TD, TH, THead, TR } from '@/components/ui/table'
import { ActionsCard } from '@/features/actions/ActionsCard'
import { hasPermission, useMe } from '@/features/auth/api'
import {
  openDownload,
  useDeleteDocument,
  useDocument,
  useParts,
  useReprocessDocument,
  useTimeline,
} from '@/features/documents/api'
import { PartsCard } from '@/features/documents/PartsCard'
import { ProcessingTimeline } from '@/features/documents/ProcessingTimeline'
import { DocumentStatusBadge } from '@/features/documents/StatusBadges'
import { ACTIVE_STATUSES, REPROCESSABLE, type DocumentDetail, type Page } from '@/features/documents/types'
import { EnrichmentPanel } from '@/features/enrichment/EnrichmentPanel'
import { useProcessingResult } from '@/features/extraction/api'
import { FieldsPanel } from '@/features/extraction/FieldsPanel'
import type { FieldValue } from '@/features/extraction/types'
import { isRouteTrace, RouteTraceView } from '@/features/routing/RouteTrace'
import { DocumentViewer, type Highlight } from '@/features/viewer/DocumentViewer'
import { formatBytes, formatDateTime } from '@/lib/format'

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-3 gap-3 py-2 text-sm">
      <dt className="text-muted">{label}</dt>
      <dd className="col-span-2 min-w-0 break-all">{children}</dd>
    </div>
  )
}

function DetailsCard({ doc }: { doc: DocumentDetail }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Details</CardTitle>
      </CardHeader>
      <CardContent>
        <dl className="divide-y divide-border">
          <Row label="Detected type">{doc.detected_mime_type}</Row>
          <Row label="Declared type">{doc.declared_mime_type ?? '—'}</Row>
          <Row label="Size">{formatBytes(doc.size_bytes)}</Row>
          <Row label="Received">{formatDateTime(doc.received_at)}</Row>
          <Row label="Source">{doc.source.replaceAll('_', ' ')}</Row>
          <Row label="Malware scan">
            {doc.scan_status === 'not_scanned' ? (
              <Badge tone="warning">
                <ShieldAlert aria-hidden />
                not scanned
              </Badge>
            ) : (
              <Badge tone={doc.scan_status === 'clean' ? 'success' : 'danger'}>{doc.scan_status}</Badge>
            )}
          </Row>
          <Row label="SHA-256">
            <span className="font-mono text-xs">{doc.sha256}</span>
          </Row>
        </dl>
      </CardContent>
    </Card>
  )
}

function TextSourceBadge({ page }: { page: Page }) {
  if (page.text_source === 'native') {
    return <Badge tone="success">native · {page.word_count ?? page.char_count} words</Badge>
  }
  if (page.text_source === 'ocr') {
    const conf = page.ocr_confidence !== null ? ` · ${Math.round(page.ocr_confidence * 100)}%` : ''
    return <Badge tone="accent">OCR{conf}</Badge>
  }
  if (page.ocr_status === 'not_configured') {
    return <Badge tone="warning">image only · OCR not configured</Badge>
  }
  if (page.text_source === 'none') return <Badge tone="warning">no readable text</Badge>
  return page.has_text_layer ? (
    <Badge tone="neutral">text layer detected</Badge>
  ) : (
    <Badge tone="warning">image only · OCR needed</Badge>
  )
}

function PagesCard({ doc }: { doc: DocumentDetail }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Pages {doc.page_count !== null ? `(${doc.page_count})` : ''}</CardTitle>
      </CardHeader>
      {doc.pages.length === 0 ? (
        <CardContent className="text-sm text-muted">Pages appear once the probe step has run.</CardContent>
      ) : (
        <Table>
          <THead>
            <tr>
              <TH>#</TH>
              <TH>Size</TH>
              <TH>Rotation</TH>
              <TH>Text layer</TH>
            </tr>
          </THead>
          <tbody>
            {doc.pages.map((p) => (
              <TR key={p.page_number}>
                <TD className="font-mono text-xs">{p.page_number}</TD>
                <TD className="font-mono text-xs">
                  {Math.round(p.width)} × {Math.round(p.height)} {p.unit}
                </TD>
                <TD className="font-mono text-xs">{p.rotation}°</TD>
                <TD>
                  <TextSourceBadge page={p} />
                </TD>
              </TR>
            ))}
          </tbody>
        </Table>
      )}
    </Card>
  )
}

export function DocumentDetailPage() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const { data: me } = useMe()
  const document = useDocument(id)
  const active = document.data ? ACTIVE_STATUSES.includes(document.data.status) : false
  const timeline = useTimeline(id, active)
  const parts = useParts(id, active)
  const result = useProcessingResult(id, active)
  const [selectedField, setSelectedField] = useState<FieldValue | null>(null)
  const highlight: Highlight | null =
    selectedField?.provenance.bbox && selectedField.provenance.page
      ? { page: selectedField.provenance.page, bbox: selectedField.provenance.bbox, label: selectedField.path }
      : null

  function selectField(field: FieldValue) {
    setSelectedField(field)
    if (field.provenance.page) setViewerPage(field.provenance.page)
  }
  const reprocess = useReprocessDocument(id)
  const remove = useDeleteDocument(id)
  const [actionError, setActionError] = useState<unknown>(null)
  const [viewerPage, setViewerPage] = useState(1)
  const canWrite = hasPermission(me, 'documents:write')

  async function onDownload() {
    setActionError(null)
    try {
      await openDownload(id)
    } catch (error) {
      setActionError(error)
    }
  }

  function onDelete() {
    if (!window.confirm('Delete this document? It will no longer appear in lists.')) return
    remove.mutate(undefined, {
      onSuccess: () => void navigate('/documents'),
      onError: setActionError,
    })
  }

  if (document.isError) {
    return <ErrorNotice error={document.error} title="Unable to load document" />
  }
  const doc = document.data

  return (
    <>
      <Link to="/documents" className="mb-3 inline-flex items-center gap-1 text-xs text-muted hover:text-ink">
        <ArrowLeft aria-hidden className="size-3.5" />
        Documents
      </Link>
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          {doc ? (
            <>
              <h1 className="truncate text-lg font-semibold tracking-tight">{doc.original_filename}</h1>
              <div className="mt-1.5 flex items-center gap-2">
                <DocumentStatusBadge status={doc.status} />
                <span className="font-mono text-[11px] text-muted">{doc.id}</span>
              </div>
            </>
          ) : (
            <Skeleton className="h-10 w-72" />
          )}
        </div>
        {doc ? (
          <div className="flex gap-2">
            <Button variant="secondary" size="sm" onClick={() => void onDownload()}>
              <Download />
              Download
            </Button>
            {canWrite ? (
              <>
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={!REPROCESSABLE.includes(doc.status) || reprocess.isPending}
                  title={REPROCESSABLE.includes(doc.status) ? undefined : 'Available once processing has finished'}
                  onClick={() => reprocess.mutate(undefined, { onError: setActionError })}
                >
                  <RotateCw />
                  {doc.status === 'FAILED' ? 'Replay' : 'Reprocess'}
                </Button>
                <Button variant="ghost" size="sm" disabled={active || remove.isPending} onClick={onDelete}>
                  <Trash2 />
                  Delete
                </Button>
              </>
            ) : null}
          </div>
        ) : null}
      </div>
      {actionError ? (
        <div className="mb-6">
          <ErrorNotice error={actionError} />
        </div>
      ) : null}

      {doc ? (
        <div className="grid gap-6 lg:grid-cols-5">
          <div className="lg:col-span-3">
            {doc.pages.some((p) => p.image_width) ? (
              <Card className="h-[78vh] overflow-hidden">
                <DocumentViewer
                  className="h-full"
                  documentId={doc.id}
                  pageCount={doc.page_count ?? 1}
                  page={viewerPage}
                  onPageChange={setViewerPage}
                  highlights={highlight ? [highlight] : []}
                />
              </Card>
            ) : (
              <Card>
                <CardContent className="text-sm text-muted">
                  The page viewer is available once the document has been digitized.
                </CardContent>
              </Card>
            )}
          </div>
          <div className="space-y-6 lg:col-span-2">
            <ActionsCard documentId={doc.id} awaitingApproval={doc.status === 'READY_FOR_ACTION'} />
            <PartsCard parts={parts.data ?? []} onSelect={setViewerPage} />
            {result.data?.parts
              .filter((p) => p.extraction)
              .map((part) => (
                <Card key={part.part_id} className="overflow-hidden">
                  <CardHeader>
                    <div>
                      <CardTitle>{part.classification.document_type_name ?? 'Extracted data'}</CardTitle>
                      <CardDescription>
                        pages {part.pages[0]}–{part.pages[1]} · click a value to see its source
                      </CardDescription>
                    </div>
                  </CardHeader>
                  <FieldsPanel part={part} selectedId={selectedField?.id ?? null} onSelect={selectField} />
                  {part.enrichment.length > 0 ? (
                    <div className="border-t border-border">
                      <p className="px-4 pt-3 text-[11px] font-semibold tracking-wide text-muted uppercase">Lookups</p>
                      <EnrichmentPanel outcomes={part.enrichment} />
                    </div>
                  ) : null}
                  {isRouteTrace(part.extraction?.route_trace) ? (
                    <details className="border-t border-border px-4 py-3">
                      <summary className="cursor-pointer text-xs font-medium">How this was extracted</summary>
                      <div className="mt-3">
                        <RouteTraceView trace={part.extraction.route_trace} />
                      </div>
                    </details>
                  ) : null}
                </Card>
              ))}
            <DetailsCard doc={doc} />
            <PagesCard doc={doc} />
          </div>
          <div className="lg:col-span-5">
            {timeline.data ? (
              <ProcessingTimeline timeline={timeline.data} />
            ) : timeline.isError ? (
              <ErrorNotice error={timeline.error} title="Unable to load timeline" />
            ) : (
              <Skeleton className="h-64" />
            )}
          </div>
        </div>
      ) : (
        <Skeleton className="h-96" />
      )}
    </>
  )
}
