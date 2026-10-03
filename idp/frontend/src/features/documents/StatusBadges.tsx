import { CircleCheck, CircleX, Clock, LoaderCircle, RotateCw, Skull, UserCheck } from 'lucide-react'

import { Badge, type BadgeProps } from '@/components/ui/badge'
import type { DocumentStatus, JobStatus, StepStatus } from '@/features/documents/types'

type Tone = NonNullable<BadgeProps['tone']>

const DOCUMENT: Record<DocumentStatus, { tone: Tone; label: string }> = {
  RECEIVED: { tone: 'neutral', label: 'Received' },
  QUEUED: { tone: 'neutral', label: 'Queued' },
  PROCESSING: { tone: 'accent', label: 'Processing' },
  WAITING_FOR_HUMAN: { tone: 'warning', label: 'Needs review' },
  READY_FOR_ACTION: { tone: 'accent', label: 'Ready for action' },
  COMPLETED: { tone: 'success', label: 'Completed' },
  FAILED: { tone: 'danger', label: 'Failed' },
  REJECTED: { tone: 'danger', label: 'Rejected' },
}

export function DocumentStatusBadge({ status }: { status: DocumentStatus }) {
  const { tone, label } = DOCUMENT[status]
  return (
    <Badge tone={tone}>
      {status === 'PROCESSING' ? <LoaderCircle aria-hidden className="animate-spin" /> : null}
      {status === 'WAITING_FOR_HUMAN' ? <UserCheck aria-hidden /> : null}
      {label}
    </Badge>
  )
}

const JOB: Record<JobStatus, { tone: Tone; label: string; Icon: typeof Clock }> = {
  QUEUED: { tone: 'neutral', label: 'Queued', Icon: Clock },
  RUNNING: { tone: 'accent', label: 'Running', Icon: LoaderCircle },
  RETRY_SCHEDULED: { tone: 'warning', label: 'Retry scheduled', Icon: RotateCw },
  SUCCEEDED: { tone: 'success', label: 'Succeeded', Icon: CircleCheck },
  FAILED: { tone: 'danger', label: 'Failed', Icon: CircleX },
  DEAD_LETTERED: { tone: 'danger', label: 'Dead-lettered', Icon: Skull },
}

export function JobStatusBadge({ status }: { status: JobStatus }) {
  const { tone, label, Icon } = JOB[status]
  return (
    <Badge tone={tone}>
      <Icon aria-hidden className={status === 'RUNNING' ? 'animate-spin' : undefined} />
      {label}
    </Badge>
  )
}

export function StepStatusIcon({ status }: { status: StepStatus }) {
  if (status === 'SUCCEEDED') return <CircleCheck aria-label="succeeded" className="size-4 text-success" />
  if (status === 'FAILED') return <CircleX aria-label="failed" className="size-4 text-danger" />
  return <LoaderCircle aria-label="running" className="size-4 animate-spin text-accent" />
}
