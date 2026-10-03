import { useState } from 'react'
import { Link } from 'react-router'

import { PageHeader } from '@/components/layout/PageHeader'
import { Badge } from '@/components/ui/badge'
import { Card } from '@/components/ui/card'
import { ErrorNotice, Skeleton } from '@/components/ui/feedback'
import { Select } from '@/components/ui/input'
import { Table, TD, TH, THead, TR } from '@/components/ui/table'
import { useJobs } from '@/features/processing/api'
import { formatDateTime } from '@/lib/format'

const FILTERS: Record<string, string[]> = {
  active: ['QUEUED', 'RUNNING', 'RETRY_SCHEDULED'],
  review: ['WAITING_FOR_REVIEW'],
  problems: ['FAILED', 'DEAD_LETTERED'],
  all: [],
}

const TONE: Record<string, 'neutral' | 'accent' | 'success' | 'warning' | 'danger'> = {
  QUEUED: 'neutral',
  RUNNING: 'accent',
  RETRY_SCHEDULED: 'warning',
  SUCCEEDED: 'success',
  FAILED: 'danger',
  DEAD_LETTERED: 'danger',
  WAITING_FOR_REVIEW: 'warning',
  CANCELLED: 'neutral',
}

export function ProcessingPage() {
  const [filter, setFilter] = useState('all')
  const jobs = useJobs(FILTERS[filter] ?? [])

  return (
    <>
      <PageHeader
        title="Processing"
        description="Processing jobs across all documents. Failed and dead-lettered jobs can be replayed from the document."
        actions={
          <Select aria-label="Job filter" className="w-48" value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option value="all">All recent jobs</option>
            <option value="active">Queued & running</option>
            <option value="review">Waiting for review</option>
            <option value="problems">Failed & dead-lettered</option>
          </Select>
        }
      />
      {jobs.isError ? (
        <ErrorNotice error={jobs.error} title="Unable to load jobs" />
      ) : (
        <Card>
          <Table>
            <THead>
              <tr>
                <TH>Document</TH>
                <TH>Status</TH>
                <TH>Step</TH>
                <TH>Attempts</TH>
                <TH>Workflow</TH>
                <TH>Created</TH>
              </tr>
            </THead>
            <tbody>
              {jobs.isPending ? (
                <TR>
                  <TD colSpan={6}>
                    <Skeleton className="h-5" />
                  </TD>
                </TR>
              ) : jobs.data.length === 0 ? (
                <TR>
                  <TD colSpan={6} className="text-muted">
                    No jobs match this filter.
                  </TD>
                </TR>
              ) : (
                jobs.data.map((job) => (
                  <TR key={job.id}>
                    <TD>
                      <Link to={`/documents/${job.document_id}`} className="font-medium text-accent hover:underline">
                        {job.document_name}
                      </Link>
                      <p className="text-[11px] text-muted">{job.trigger}</p>
                    </TD>
                    <TD>
                      <Badge tone={TONE[job.status] ?? 'neutral'}>{job.status.replaceAll('_', ' ')}</Badge>
                      {job.last_error_code ? <p className="mt-0.5 font-mono text-[11px] text-danger">{job.last_error_code}</p> : null}
                    </TD>
                    <TD className="font-mono text-xs">{job.current_step ?? '—'}</TD>
                    <TD className="text-xs">
                      {job.attempts}/{job.max_attempts}
                      {job.next_attempt_at ? <p className="text-[11px] text-muted">next {formatDateTime(job.next_attempt_at)}</p> : null}
                    </TD>
                    <TD className="font-mono text-xs">{job.workflow}</TD>
                    <TD className="text-xs text-muted">{formatDateTime(job.created_at)}</TD>
                  </TR>
                ))
              )}
            </tbody>
          </Table>
        </Card>
      )}
    </>
  )
}
