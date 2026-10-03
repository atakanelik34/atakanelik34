import { ArrowLeft, Download, Play } from 'lucide-react'
import { useState } from 'react'
import { Link, useParams } from 'react-router'

import { PageHeader } from '@/components/layout/PageHeader'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { ErrorNotice, Skeleton, Spinner } from '@/components/ui/feedback'
import { Table, TD, TH, THead, TR } from '@/components/ui/table'
import { hasPermission, useMe } from '@/features/auth/api'
import { useDataset, useImportReviews, useRun, useRuns, useStartRun, type RunMetrics } from '@/features/evaluation/api'
import { pct } from '@/features/evaluation/format'
import { formatDateTime, formatDuration } from '@/lib/format'

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-[11px] text-muted">{label}</p>
      <p className="text-lg font-semibold">{value}</p>
    </div>
  )
}

function Summary({ metrics }: { metrics: RunMetrics }) {
  return (
    <CardContent className="grid grid-cols-2 gap-4 sm:grid-cols-4">
      <Stat label="F1" value={pct(metrics.f1)} />
      <Stat label="Precision" value={pct(metrics.precision)} />
      <Stat label="Recall" value={pct(metrics.recall)} />
      <Stat label="Normalized match" value={pct(metrics.normalized_match)} />
      <Stat label="Intervention rate" value={pct(metrics.intervention_rate)} />
      <Stat label="Overconfident errors" value={String(metrics.overconfident)} />
      <Stat label="Cost / document" value={metrics.cost_per_document === null ? '—' : metrics.cost_per_document.toFixed(4)} />
      <Stat label="Latency / document" value={formatDuration(metrics.latency_ms_per_document)} />
    </CardContent>
  )
}

export function DatasetPage() {
  const { id = '' } = useParams()
  const { data: me } = useMe()
  const dataset = useDataset(id)
  const runs = useRuns(id)
  const importReviews = useImportReviews(id)
  const startRun = useStartRun(id)
  const [selected, setSelected] = useState<string | null>(null)
  const runId = selected ?? runs.data?.[0]?.id ?? null
  const run = useRun(runId)
  const canWrite = hasPermission(me, 'config:write')

  if (dataset.isError) return <ErrorNotice error={dataset.error} title="Unable to load dataset" />
  if (!dataset.data) return <Skeleton className="h-64" />

  return (
    <>
      <PageHeader
        title={dataset.data.name}
        description={`${dataset.data.items} items · ${dataset.data.document_type ?? 'any document type'}`}
        actions={
          <div className="flex items-center gap-2">
            <Link to="/evaluation" className="text-muted hover:text-ink" aria-label="Back to datasets">
              <ArrowLeft className="size-4" />
            </Link>
            {canWrite ? (
              <>
                <Button variant="secondary" disabled={importReviews.isPending} onClick={() => importReviews.mutate(undefined)}>
                  {importReviews.isPending ? <Spinner /> : <Download />}
                  Import approved reviews
                </Button>
                <Button
                  disabled={startRun.isPending || dataset.data.items === 0}
                  onClick={() => startRun.mutate(undefined, { onSuccess: (r) => setSelected(r.id) })}
                >
                  {startRun.isPending ? <Spinner /> : <Play />}
                  Run evaluation
                </Button>
              </>
            ) : null}
          </div>
        }
      />
      <div className="space-y-6">
        {importReviews.data ? (
          <output className="block text-sm text-muted">{importReviews.data.added} new item(s) imported.</output>
        ) : null}
        {importReviews.isError ? <ErrorNotice error={importReviews.error} /> : null}
        {startRun.isError ? <ErrorNotice error={startRun.error} /> : null}
        {run.data ? (
          <>
            <Card>
              <CardHeader>
                <CardTitle>Run {formatDateTime(run.data.created_at)}</CardTitle>
                <span className="text-xs text-muted">
                  {Object.entries(run.data.config)
                    .map(([k, v]) => `${k}: ${v.join(', ')}`)
                    .join(' · ')}
                </span>
              </CardHeader>
              <Summary metrics={run.data.metrics} />
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>Per field</CardTitle>
              </CardHeader>
              <Table>
                <THead>
                  <tr>
                    <TH>Field</TH>
                    <TH>Compared</TH>
                    <TH>Precision</TH>
                    <TH>Recall</TH>
                    <TH>F1</TH>
                    <TH>Confidence (right / wrong)</TH>
                  </tr>
                </THead>
                <tbody>
                  {Object.entries(run.data.field_metrics).map(([path, m]) => (
                    <TR key={path}>
                      <TD className="font-mono text-xs">{path}</TD>
                      <TD className="text-xs">{m.compared}</TD>
                      <TD className="text-xs">{pct(m.precision)}</TD>
                      <TD className="text-xs">{pct(m.recall)}</TD>
                      <TD className="text-xs">
                        <Badge tone={m.f1 === null ? 'neutral' : m.f1 >= 0.95 ? 'success' : m.f1 >= 0.8 ? 'warning' : 'danger'}>{pct(m.f1)}</Badge>
                      </TD>
                      <TD className="text-xs text-muted">
                        {pct(m.mean_confidence_correct)} / {pct(m.mean_confidence_incorrect)}
                      </TD>
                    </TR>
                  ))}
                </tbody>
              </Table>
            </Card>
          </>
        ) : runs.data?.length === 0 ? (
          <Card>
            <CardContent className="text-sm text-muted">No runs yet.</CardContent>
          </Card>
        ) : null}
        {runs.data && runs.data.length > 1 ? (
          <Card>
            <CardHeader>
              <CardTitle>History</CardTitle>
            </CardHeader>
            <ul className="divide-y divide-border">
              {runs.data.map((r) => (
                <li key={r.id}>
                  <button
                    type="button"
                    className="flex w-full items-center justify-between px-4 py-2 text-left text-sm hover:bg-canvas"
                    onClick={() => setSelected(r.id)}
                    aria-current={r.id === runId}
                  >
                    <span>{formatDateTime(r.created_at)}</span>
                    <span className="text-xs text-muted">F1 {pct(r.metrics.f1)}</span>
                  </button>
                </li>
              ))}
            </ul>
          </Card>
        ) : null}
      </div>
    </>
  )
}
