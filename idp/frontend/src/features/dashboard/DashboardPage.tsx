import { RefreshCw } from 'lucide-react'

import { PageHeader } from '@/components/layout/PageHeader'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { ErrorNotice, Skeleton } from '@/components/ui/feedback'
import { hasPermission, useMe } from '@/features/auth/api'
import { useSystemStatus, type WorkerInfo } from '@/features/system/api'
import { ComponentList } from '@/features/system/ComponentList'
import { StatusBadge } from '@/features/system/StatusBadge'
import { secondsAgo } from '@/lib/format'

function WorkersCard({ workers }: { workers: WorkerInfo[] }) {
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Workers</CardTitle>
          <CardDescription>Live heartbeats from processing workers</CardDescription>
        </div>
      </CardHeader>
      <CardContent className="p-0">
        {workers.length === 0 ? (
          <p className="px-5 py-6 text-sm text-muted">
            No worker is reporting. Documents can be received, but processing will wait until a
            worker starts.
          </p>
        ) : (
          <ul className="divide-y divide-border">
            {workers.map((w) => (
              <li key={w.worker_id} className="flex items-center justify-between px-5 py-2.5 text-sm">
                <span className="font-mono text-xs">{w.worker_id}</span>
                <span className="text-xs text-muted">
                  v{w.version} · {w.max_jobs} slots · seen {secondsAgo(w.last_seen_at)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}

export function DashboardPage() {
  const { data: me } = useMe()
  const canReadSystem = hasPermission(me, 'system:read')
  const status = useSystemStatus(canReadSystem)

  const workers =
    (status.data?.components.find((c) => c.name === 'workers')?.metadata.workers as
      | WorkerInfo[]
      | undefined) ?? []

  return (
    <>
      <PageHeader
        title="Dashboard"
        description="Platform health. Processing metrics appear here once document ingestion is enabled."
        actions={
          canReadSystem ? (
            <Button
              variant="secondary"
              size="sm"
              onClick={() => void status.refetch()}
              disabled={status.isFetching}
            >
              <RefreshCw className={status.isFetching ? 'animate-spin' : undefined} />
              Refresh
            </Button>
          ) : null
        }
      />

      {!canReadSystem ? (
        <Card>
          <CardContent className="text-sm text-muted">
            Your role does not include access to system status.
          </CardContent>
        </Card>
      ) : status.isError ? (
        <ErrorNotice error={status.error} title="Unable to load system status" />
      ) : (
        <div className="grid gap-6 lg:grid-cols-3">
          <Card className="lg:col-span-2">
            <CardHeader>
              <div>
                <CardTitle>System status</CardTitle>
                <CardDescription>
                  {status.data
                    ? `${status.data.environment} · v${status.data.version} · refreshed every 15s`
                    : 'Checking components…'}
                </CardDescription>
              </div>
              {status.data ? <StatusBadge status={status.data.status} /> : null}
            </CardHeader>
            {status.data ? (
              <ComponentList components={status.data.components} />
            ) : (
              <CardContent className="space-y-3">
                {[0, 1, 2, 3].map((i) => (
                  <Skeleton key={i} className="h-9" />
                ))}
              </CardContent>
            )}
          </Card>
          <WorkersCard workers={workers} />
        </div>
      )}
    </>
  )
}
