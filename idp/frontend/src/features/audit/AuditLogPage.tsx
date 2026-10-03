import { useInfiniteQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { PageHeader } from '@/components/layout/PageHeader'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { ErrorNotice, Skeleton } from '@/components/ui/feedback'
import { Input } from '@/components/ui/input'
import { Table, TD, TH, THead, TR } from '@/components/ui/table'
import { apiRequest } from '@/lib/api'
import { formatDateTime } from '@/lib/format'

interface AuditEntry {
  id: string
  occurred_at: string
  actor_type: string
  actor_id: string | null
  actor_email: string | null
  action: string
  entity_type: string
  entity_id: string | null
  before: Record<string, unknown> | null
  after: Record<string, unknown> | null
  correlation_id: string | null
  ip_address: string | null
}

function summary(entry: AuditEntry): string {
  const after = entry.after ?? {}
  const before = entry.before ?? {}
  if ('status' in after && 'status' in before) return `${String(before.status)} → ${String(after.status)}`
  return Object.entries(after)
    .slice(0, 3)
    .map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : String(v)}`)
    .join(' · ')
}

export function AuditLogPage() {
  const [action, setAction] = useState('')
  const [entity, setEntity] = useState('')
  const logs = useInfiniteQuery({
    queryKey: ['audit', action, entity],
    queryFn: ({ pageParam, signal }) => {
      const params = new URLSearchParams({ limit: '50' })
      if (action) params.set('action', action)
      if (entity) params.set('entity_id', entity)
      if (pageParam) params.set('cursor', pageParam)
      return apiRequest<{ items: AuditEntry[]; next_cursor: string | null }>(`/audit-logs?${params}`, { signal })
    },
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
  })
  const items = logs.data?.pages.flatMap((p) => p.items) ?? []

  return (
    <>
      <PageHeader
        title="Audit log"
        description="Append-only record of every significant action (enforced by the database)."
        actions={
          <>
            <Input aria-label="Action prefix" placeholder="action, e.g. review." className="w-48" value={action} onChange={(e) => setAction(e.target.value)} />
            <Input aria-label="Entity id" placeholder="entity id" className="w-64" value={entity} onChange={(e) => setEntity(e.target.value.trim())} />
          </>
        }
      />
      {logs.isError ? (
        <ErrorNotice error={logs.error} title="Unable to load the audit log" />
      ) : (
        <Card>
          <Table>
            <THead>
              <tr>
                <TH>When</TH>
                <TH>Actor</TH>
                <TH>Action</TH>
                <TH>Entity</TH>
                <TH>Change</TH>
              </tr>
            </THead>
            <tbody>
              {logs.isPending ? (
                <TR>
                  <TD colSpan={5}>
                    <Skeleton className="h-5" />
                  </TD>
                </TR>
              ) : (
                items.map((e) => (
                  <TR key={e.id}>
                    <TD className="text-xs whitespace-nowrap text-muted">{formatDateTime(e.occurred_at)}</TD>
                    <TD className="text-xs">{e.actor_email ?? e.actor_type}</TD>
                    <TD className="font-mono text-xs">{e.action}</TD>
                    <TD className="font-mono text-[11px] text-muted">
                      {e.entity_type}
                      {e.entity_id ? `:${e.entity_id.slice(0, 8)}` : ''}
                    </TD>
                    <TD className="max-w-sm truncate text-xs text-muted" title={e.correlation_id ?? undefined}>
                      {summary(e)}
                    </TD>
                  </TR>
                ))
              )}
            </tbody>
          </Table>
          {logs.hasNextPage ? (
            <div className="border-t border-border p-3 text-center">
              <Button variant="secondary" size="sm" onClick={() => void logs.fetchNextPage()}>
                Load more
              </Button>
            </div>
          ) : null}
        </Card>
      )}
    </>
  )
}
