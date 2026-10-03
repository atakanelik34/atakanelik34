import { useState } from 'react'
import { useNavigate } from 'react-router'

import { PageHeader } from '@/components/layout/PageHeader'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { ErrorNotice, Skeleton } from '@/components/ui/feedback'
import { Select } from '@/components/ui/input'
import { Table, TD, TH, THead, TR } from '@/components/ui/table'
import { hasPermission, useMe } from '@/features/auth/api'
import { useDocuments } from '@/features/documents/api'
import { DocumentStatusBadge } from '@/features/documents/StatusBadges'
import { DOCUMENT_STATUSES, type DocumentStatus } from '@/features/documents/types'
import { UploadPanel } from '@/features/documents/UploadPanel'
import { formatBytes, formatDateTime } from '@/lib/format'

const MIME_LABEL: Record<string, string> = {
  'application/pdf': 'PDF',
  'image/png': 'PNG',
  'image/jpeg': 'JPEG',
  'image/tiff': 'TIFF',
}

export function DocumentsPage() {
  const { data: me } = useMe()
  const navigate = useNavigate()
  const [status, setStatus] = useState<DocumentStatus | null>(null)
  const canRead = hasPermission(me, 'documents:read')
  const canWrite = hasPermission(me, 'documents:write')
  const documents = useDocuments(status, canRead)
  const items = documents.data?.pages.flatMap((p) => p.items) ?? []

  return (
    <>
      <PageHeader
        title="Documents"
        description="Every file received, its processing status, and its history."
        actions={
          <Select
            aria-label="Filter by status"
            className="w-48"
            value={status ?? ''}
            onChange={(e) => setStatus((e.target.value || null) as DocumentStatus | null)}
          >
            <option value="">All statuses</option>
            {DOCUMENT_STATUSES.map((s) => (
              <option key={s} value={s}>
                {s.replaceAll('_', ' ').toLowerCase()}
              </option>
            ))}
          </Select>
        }
      />
      {canWrite ? (
        <div className="mb-6">
          <UploadPanel />
        </div>
      ) : null}

      {documents.isError ? (
        <ErrorNotice error={documents.error} title="Unable to load documents" />
      ) : (
        <Card>
          <Table>
            <THead>
              <tr>
                <TH>Name</TH>
                <TH>Type</TH>
                <TH className="text-right">Pages</TH>
                <TH className="text-right">Size</TH>
                <TH>Status</TH>
                <TH>Received</TH>
              </tr>
            </THead>
            <tbody>
              {documents.isPending ? (
                [0, 1, 2].map((i) => (
                  <TR key={i}>
                    <TD colSpan={6}>
                      <Skeleton className="h-5" />
                    </TD>
                  </TR>
                ))
              ) : items.length === 0 ? (
                <TR>
                  <TD colSpan={6} className="py-10 text-center text-muted">
                    {status ? 'No documents with this status.' : 'No documents yet.'}
                  </TD>
                </TR>
              ) : (
                items.map((doc) => (
                  <TR
                    key={doc.id}
                    className="cursor-pointer hover:bg-canvas/70"
                    onClick={() => void navigate(`/documents/${doc.id}`)}
                  >
                    <TD className="max-w-xs truncate font-medium">
                      <a
                        href={`/documents/${doc.id}`}
                        onClick={(e) => {
                          e.preventDefault()
                          void navigate(`/documents/${doc.id}`)
                        }}
                        className="hover:text-accent"
                      >
                        {doc.original_filename}
                      </a>
                    </TD>
                    <TD className="text-muted">{MIME_LABEL[doc.detected_mime_type] ?? doc.detected_mime_type}</TD>
                    <TD className="text-right font-mono text-xs">{doc.page_count ?? '—'}</TD>
                    <TD className="text-right font-mono text-xs">{formatBytes(doc.size_bytes)}</TD>
                    <TD>
                      <DocumentStatusBadge status={doc.status} />
                    </TD>
                    <TD className="text-muted">{formatDateTime(doc.received_at)}</TD>
                  </TR>
                ))
              )}
            </tbody>
          </Table>
          {documents.hasNextPage ? (
            <div className="border-t border-border p-3 text-center">
              <Button
                variant="secondary"
                size="sm"
                onClick={() => void documents.fetchNextPage()}
                disabled={documents.isFetchingNextPage}
              >
                Load more
              </Button>
            </div>
          ) : null}
        </Card>
      )}
    </>
  )
}
