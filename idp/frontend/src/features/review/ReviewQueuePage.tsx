import { useState } from 'react'
import { Link } from 'react-router'

import { PageHeader } from '@/components/layout/PageHeader'
import { Badge } from '@/components/ui/badge'
import { Card } from '@/components/ui/card'
import { ErrorNotice, Skeleton } from '@/components/ui/feedback'
import { Select } from '@/components/ui/input'
import { Table, TD, TH, THead, TR } from '@/components/ui/table'
import { useReviewQueue } from '@/features/review/api'
import { formatDateTime } from '@/lib/format'

const STATUS_TONE = {
  open: 'warning',
  in_progress: 'accent',
  approved: 'success',
  rejected: 'danger',
  sent_back: 'neutral',
} as const

export function ReviewQueuePage() {
  const [status, setStatus] = useState<string | null>(null)
  const queue = useReviewQueue(status)

  return (
    <>
      <PageHeader
        title="Review queue"
        description="Documents waiting for a human decision. Review is a normal step, not a failure."
        actions={
          <Select aria-label="Filter" className="w-44" value={status ?? ''} onChange={(e) => setStatus(e.target.value || null)}>
            <option value="">Open & in progress</option>
            <option value="approved">Approved</option>
            <option value="rejected">Rejected</option>
            <option value="sent_back">Sent back</option>
          </Select>
        }
      />
      {queue.isError ? (
        <ErrorNotice error={queue.error} title="Unable to load the review queue" />
      ) : (
        <Card>
          <Table>
            <THead>
              <tr>
                <TH>Document</TH>
                <TH>Why</TH>
                <TH>Status</TH>
                <TH>Waiting since</TH>
              </tr>
            </THead>
            <tbody>
              {queue.isPending ? (
                <TR>
                  <TD colSpan={4}>
                    <Skeleton className="h-5" />
                  </TD>
                </TR>
              ) : queue.data.length === 0 ? (
                <TR>
                  <TD colSpan={4} className="py-10 text-center text-muted">
                    Nothing to review.
                  </TD>
                </TR>
              ) : (
                queue.data.map((task) => (
                  <TR key={task.id}>
                    <TD className="font-medium">
                      <Link to={`/reviews/${task.id}`} className="hover:text-accent">
                        {task.document_name}
                      </Link>
                    </TD>
                    <TD className="max-w-md text-xs text-muted">
                      <span className="line-clamp-2">
                        {task.reasons.slice(0, 3).map((r) => r.message).join(' · ')}
                        {task.reasons.length > 3 ? ` · +${task.reasons.length - 3} more` : ''}
                      </span>
                    </TD>
                    <TD>
                      <Badge tone={STATUS_TONE[task.status]}>{task.status.replace('_', ' ')}</Badge>
                    </TD>
                    <TD className="text-muted">{formatDateTime(task.created_at)}</TD>
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
