import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { ErrorNotice, Spinner } from '@/components/ui/feedback'
import { useResolve } from '@/features/review/api'

type Decision = 'approve' | 'reject' | 'send-back'

const LABELS: Record<Decision, string> = {
  approve: 'Approve document',
  reject: 'Reject document',
  'send-back': 'Send back for reprocessing',
}

export function DecisionPanel({ taskId, blocking, onDone }: { taskId: string; blocking: number; onDone: () => void }) {
  const resolve = useResolve(taskId)
  const [decision, setDecision] = useState<Decision | null>(null)
  const [text, setText] = useState('')
  const needsReason = decision === 'reject' || decision === 'send-back'

  return (
    <div className="space-y-3 px-4 py-3">
      {blocking > 0 ? (
        <p className="text-xs text-warning">
          {blocking} check(s) still need attention. Approving records them as overridden by you.
        </p>
      ) : (
        <p className="text-xs text-success">All blocking checks are resolved.</p>
      )}
      {resolve.isError ? <ErrorNotice error={resolve.error} /> : null}
      {decision === null ? (
        <div className="flex flex-col gap-2">
          <Button onClick={() => setDecision('approve')}>Approve</Button>
          <div className="flex gap-2">
            <Button className="flex-1" variant="secondary" onClick={() => setDecision('send-back')}>
              Send back
            </Button>
            <Button className="flex-1" variant="danger" onClick={() => setDecision('reject')}>
              Reject
            </Button>
          </div>
        </div>
      ) : (
        <div className="space-y-2">
          <p className="text-sm font-medium">{LABELS[decision]}</p>
          <textarea
            aria-label={needsReason ? 'Reason' : 'Note'}
            placeholder={needsReason ? 'Reason (required)' : 'Note (optional)'}
            className="h-20 w-full rounded-md border border-border p-2 text-sm"
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          <div className="flex gap-2">
            <Button
              size="sm"
              variant={decision === 'reject' ? 'danger' : 'primary'}
              disabled={resolve.isPending || (needsReason && text.trim().length < 3)}
              onClick={() => resolve.mutate({ decision, text }, { onSuccess: onDone })}
            >
              {resolve.isPending ? <Spinner /> : null}
              Confirm
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setDecision(null)}>
              Cancel
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
