import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { JobStatusBadge, StepStatusIcon } from '@/features/documents/StatusBadges'
import type { StatusChange, Timeline } from '@/features/documents/types'
import { formatDateTime, formatDuration } from '@/lib/format'

function metricsLabel(metrics: Record<string, unknown>): string {
  return Object.entries(metrics)
    .map(([k, v]) => `${k.replaceAll('_', ' ')}: ${String(v)}`)
    .join(' · ')
}

function StatusHistory({ changes }: { changes: StatusChange[] }) {
  return (
    <ol className="space-y-2">
      {changes.map((c, i) => (
        <li key={`${c.occurred_at}-${i}`} className="flex items-baseline gap-3 text-xs">
          <span className="w-36 shrink-0 text-muted">{formatDateTime(c.occurred_at)}</span>
          <span className="font-mono">
            {c.from_status} → <span className="font-semibold">{c.to_status}</span>
          </span>
          <span className="truncate text-muted">
            {c.reason ?? ''} {c.actor_type === 'system' ? '· system' : '· user'}
          </span>
        </li>
      ))}
    </ol>
  )
}

export function ProcessingTimeline({ timeline }: { timeline: Timeline }) {
  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Processing runs</CardTitle>
            <CardDescription>Each run executes a pinned workflow version; history is never overwritten.</CardDescription>
          </div>
        </CardHeader>
        <CardContent className="space-y-5">
          {timeline.jobs.length === 0 ? <p className="text-sm text-muted">No runs yet.</p> : null}
          {timeline.jobs.map((job) => (
            <section key={job.id} aria-label={`Run ${job.id}`} className="rounded-md border border-border">
              <header className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-border bg-canvas/50 px-4 py-2.5">
                <JobStatusBadge status={job.status} />
                <span className="text-xs font-medium">
                  {job.workflow_key} v{job.workflow_version}
                </span>
                <span className="text-xs text-muted">
                  {job.trigger} · attempt {job.attempts}/{job.max_attempts} · {formatDateTime(job.created_at)}
                </span>
                {job.status === 'RETRY_SCHEDULED' && job.next_attempt_at ? (
                  <span className="text-xs text-warning">next attempt {formatDateTime(job.next_attempt_at)}</span>
                ) : null}
                <span className="ml-auto font-mono text-[10px] text-muted">{job.correlation_id ?? ''}</span>
              </header>
              {job.last_error_code && job.status !== 'SUCCEEDED' ? (
                <p className="border-b border-border px-4 py-2 text-xs text-danger">
                  <span className="font-mono">{job.last_error_category}</span> · {job.last_error_message}
                </p>
              ) : null}
              <ul className="divide-y divide-border">
                {job.steps.map((step) => (
                  <li key={step.id} className="flex items-start gap-3 px-4 py-2.5 text-xs">
                    <StepStatusIcon status={step.status} />
                    <div className="min-w-0 flex-1">
                      <p className="font-medium">
                        {step.step_key}
                        <span className="ml-2 font-normal text-muted">attempt {step.attempt}</span>
                      </p>
                      {step.error_code ? (
                        <p className="text-danger">
                          {step.error_category} · {step.error_message}
                        </p>
                      ) : (
                        <p className="truncate text-muted">{metricsLabel(step.metrics)}</p>
                      )}
                    </div>
                    <span className="text-right text-muted">
                      {step.provider ? (
                        <span className="block font-mono text-[10px]">{step.provider}</span>
                      ) : null}
                      {formatDuration(step.duration_ms)}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Status history</CardTitle>
            <CardDescription>From the append-only audit log.</CardDescription>
          </div>
        </CardHeader>
        <CardContent>
          <StatusHistory changes={timeline.status_changes} />
        </CardContent>
      </Card>
    </div>
  )
}
