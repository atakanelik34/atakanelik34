import { useState, type FormEvent } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { ErrorNotice, Skeleton, Spinner } from '@/components/ui/feedback'
import { Input, Label, Select } from '@/components/ui/input'
import { hasPermission, useMe } from '@/features/auth/api'
import { usePolicy, useUpdatePolicy, type PolicyState, type ProcessingMode } from '@/features/processing/api'

export const MODE_TEXT: Record<ProcessingMode, string> = {
  LOCAL_ONLY: 'Document content never leaves this deployment. Cloud providers are refused by the router and the gateway.',
  HYBRID: 'Local providers first; cloud providers only as a later fallback.',
  CLOUD_ALLOWED: 'Cloud providers may be used whenever the router selects them.',
}

function PolicyForm({ state }: { state: PolicyState }) {
  const update = useUpdatePolicy()
  const [mode, setMode] = useState<ProcessingMode>(state.requested.mode)
  const [allowLlm, setAllowLlm] = useState(state.requested.allow_llm)
  const [allowMock, setAllowMock] = useState(state.requested.allow_mock_providers)
  const [limit, setLimit] = useState(state.requested.max_cost_per_document?.toString() ?? '')

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    update.mutate({ mode, allow_llm: allowLlm, allow_mock_providers: allowMock, max_cost_per_document: limit === '' ? null : Number(limit) })
  }

  return (
    <form onSubmit={submit} className="space-y-3 border-t border-border pt-3">
      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <Label htmlFor="policy-mode">Mode</Label>
          <Select id="policy-mode" value={mode} onChange={(e) => setMode(e.target.value as ProcessingMode)} className="w-44">
            <option value="LOCAL_ONLY">Local only</option>
            <option value="HYBRID">Hybrid</option>
            <option value="CLOUD_ALLOWED">Cloud allowed</option>
          </Select>
        </div>
        <div className="space-y-1">
          <Label htmlFor="policy-limit">Cost limit / document</Label>
          <Input id="policy-limit" type="number" min="0" step="0.01" className="w-36" value={limit} onChange={(e) => setLimit(e.target.value)} placeholder="none" />
        </div>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={allowLlm} onChange={(e) => setAllowLlm(e.target.checked)} />
          Allow LLM processing
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={allowMock} onChange={(e) => setAllowMock(e.target.checked)} />
          Allow mock providers
        </label>
        <Button type="submit" size="sm" disabled={update.isPending}>
          {update.isPending ? <Spinner /> : null}
          Save policy
        </Button>
      </div>
      {update.isError ? <ErrorNotice error={update.error} /> : null}
    </form>
  )
}

/** The tenant's processing policy: what it asked for, and what the deployment allows. */
export function PolicyCard() {
  const { data: me } = useMe()
  const policy = usePolicy()
  if (policy.isError) return <ErrorNotice error={policy.error} title="Unable to load the processing policy" />
  if (!policy.data) return <Skeleton className="h-28" />
  const { effective, requested, ceiling } = policy.data
  const clamped = requested.mode !== effective.mode || requested.allow_mock_providers !== effective.allow_mock_providers

  return (
    <Card>
      <CardHeader>
        <CardTitle>Processing policy</CardTitle>
        <Badge tone={effective.mode === 'LOCAL_ONLY' ? 'success' : 'warning'}>{effective.mode.replace('_', ' ')}</Badge>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <p>{MODE_TEXT[effective.mode]}</p>
        <p className="text-xs text-muted">
          LLM processing {effective.allow_llm ? 'allowed' : 'disabled'} · mock providers {effective.allow_mock_providers ? 'allowed' : 'refused'} · cost
          limit {effective.max_cost_per_document ?? 'none'} · policy v{effective.version} · routing v{effective.routing_version}
        </p>
        {clamped ? (
          <p className="text-xs text-warning">
            Limited by the deployment: the server allows at most {ceiling.mode.replace('_', ' ')}
            {ceiling.allow_mock_providers ? '' : ' and no mock providers'}.
          </p>
        ) : null}
        {hasPermission(me, 'tenant:manage') ? <PolicyForm key={requested.version} state={policy.data} /> : null}
      </CardContent>
    </Card>
  )
}
