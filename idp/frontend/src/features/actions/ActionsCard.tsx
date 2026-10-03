import { useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { ErrorNotice, Spinner } from '@/components/ui/feedback'
import { useActionRuns, useDecideActions, type ActionRun } from '@/features/actions/api'
import { hasPermission, useMe } from '@/features/auth/api'
import { formatDateTime } from '@/lib/format'

const TONE = {
  pending_approval: 'warning',
  approved: 'accent',
  rejected: 'neutral',
  succeeded: 'success',
  failed: 'danger',
} as const satisfies Record<ActionRun['status'], string>

/** Business actions of a document: what will be (or was) sent where, and the decision. */
export function ActionsCard({ documentId, awaitingApproval }: { documentId: string; awaitingApproval: boolean }) {
  const { data: me } = useMe()
  const runs = useActionRuns(documentId, awaitingApproval)
  const decide = useDecideActions(documentId)
  const [text, setText] = useState('')
  const [open, setOpen] = useState<string | null>(null)
  if (!runs.data || runs.data.length === 0) return null
  const canDecide = awaitingApproval && hasPermission(me, 'actions:execute')

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Actions</CardTitle>
          <CardDescription>
            {awaitingApproval ? 'Waiting for approval — nothing has been sent yet.' : 'Configured actions for this document type.'}
          </CardDescription>
        </div>
      </CardHeader>
      <ul className="divide-y divide-border">
        {runs.data.map((run) => (
          <li key={run.id} className="space-y-1 px-4 py-3 text-xs">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{run.name}</span>
              <span className="font-mono text-muted">→ {run.connection_key}</span>
              <Badge tone={TONE[run.status]}>{run.status.replace('_', ' ')}</Badge>
              {run.is_mock ? <Badge tone="warning">mock</Badge> : null}
              {run.external_reference ? <span className="font-mono">{run.external_reference}</span> : null}
              {run.error_code ? <span className="font-mono text-danger">{run.error_code}</span> : null}
            </div>
            <p className="text-muted">
              {run.executed_at ? `executed ${formatDateTime(run.executed_at)}` : `created ${formatDateTime(run.created_at)}`}
              {run.decision_note ? ` · “${run.decision_note}”` : ''}
            </p>
            <button type="button" className="text-accent hover:underline" onClick={() => setOpen(open === run.id ? null : run.id)}>
              {open === run.id ? 'Hide payload' : 'Show payload'}
            </button>
            {open === run.id ? <pre className="max-h-64 overflow-auto rounded bg-canvas p-2">{JSON.stringify(run.payload, null, 2)}</pre> : null}
          </li>
        ))}
      </ul>
      {canDecide ? (
        <CardContent className="space-y-2 border-t border-border">
          <textarea
            aria-label="Decision note"
            placeholder="Note (required to reject)"
            className="h-16 w-full rounded-md border border-border p-2 text-sm"
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          <div className="flex gap-2">
            <Button size="sm" disabled={decide.isPending} onClick={() => decide.mutate({ approve: true, text })}>
              {decide.isPending ? <Spinner /> : null}
              Approve actions
            </Button>
            <Button size="sm" variant="danger" disabled={decide.isPending || text.trim().length < 3} onClick={() => decide.mutate({ approve: false, text })}>
              Reject actions
            </Button>
          </div>
          {decide.isError ? <ErrorNotice error={decide.error} /> : null}
        </CardContent>
      ) : null}
    </Card>
  )
}
