import { Copy, KeyRound } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { Link } from 'react-router'

import { PageHeader } from '@/components/layout/PageHeader'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { ErrorNotice, Skeleton, Spinner } from '@/components/ui/feedback'
import { Input, Label } from '@/components/ui/input'
import { Table, TD, TH, THead, TR } from '@/components/ui/table'
import { useApiKeys, useCreateApiKey, useRevokeApiKey } from '@/features/actions/api'
import { hasPermission, useMe } from '@/features/auth/api'
import { useDocuments } from '@/features/documents/api'
import { DocumentStatusBadge } from '@/features/documents/StatusBadges'
import { formatDateTime } from '@/lib/format'

function ApiKeys() {
  const keys = useApiKeys(true)
  const create = useCreateApiKey()
  const revoke = useRevokeApiKey()
  const [name, setName] = useState('')
  const [days, setDays] = useState('')

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    create.mutate({ name, expires_in_days: days ? Number(days) : null }, { onSuccess: () => setName('') })
  }

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>API keys</CardTitle>
          <CardDescription>For scanners and integrations. A key can upload and read documents, never more than its creator.</CardDescription>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <form onSubmit={submit} className="flex flex-wrap items-end gap-3">
          <div className="space-y-1">
            <Label htmlFor="key-name">Name</Label>
            <Input id="key-name" value={name} onChange={(e) => setName(e.target.value)} className="w-56" required maxLength={120} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="key-days">Expires after (days)</Label>
            <Input id="key-days" type="number" min="1" max="3650" value={days} onChange={(e) => setDays(e.target.value)} className="w-32" placeholder="never" />
          </div>
          <Button type="submit" size="sm" disabled={create.isPending || !name.trim()}>
            {create.isPending ? <Spinner /> : <KeyRound />}
            Create key
          </Button>
        </form>
        {create.data ? (
          <div className="space-y-1 rounded-md border border-warning bg-warning-soft p-3 text-xs">
            <p className="font-medium">Copy this key now — it will not be shown again.</p>
            <div className="flex items-center gap-2">
              <code className="break-all" data-testid="new-api-key">
                {create.data.token}
              </code>
              <button type="button" aria-label="Copy key" onClick={() => void navigator.clipboard?.writeText(create.data.token)}>
                <Copy className="size-3.5" />
              </button>
            </div>
          </div>
        ) : null}
        {create.isError ? <ErrorNotice error={create.error} /> : null}
      </CardContent>
      <Table>
        <THead>
          <tr>
            <TH>Name</TH>
            <TH>Prefix</TH>
            <TH>Last used</TH>
            <TH>Status</TH>
            <TH />
          </tr>
        </THead>
        <tbody>
          {keys.data?.map((k) => (
            <TR key={k.id}>
              <TD className="text-xs font-medium">{k.name}</TD>
              <TD className="font-mono text-xs">idp_{k.prefix}_…</TD>
              <TD className="text-xs text-muted">{k.last_used_at ? formatDateTime(k.last_used_at) : 'never'}</TD>
              <TD>
                <Badge tone={k.revoked_at ? 'neutral' : 'success'}>{k.revoked_at ? 'revoked' : 'active'}</Badge>
              </TD>
              <TD>
                {!k.revoked_at ? (
                  <Button size="sm" variant="ghost" disabled={revoke.isPending} onClick={() => revoke.mutate(k.id)}>
                    Revoke
                  </Button>
                ) : null}
              </TD>
            </TR>
          ))}
        </tbody>
      </Table>
    </Card>
  )
}

export function InboxPage() {
  const { data: me } = useMe()
  const documents = useDocuments(null, true, 'api')
  const items = documents.data?.pages.flatMap((p) => p.items) ?? []
  return (
    <>
      <PageHeader title="Inbox" description="Documents received from machines and integrations through the API." />
      <div className="space-y-6">
        <Card>
          <CardHeader>
            <CardTitle>Received via API</CardTitle>
          </CardHeader>
          {documents.isError ? (
            <CardContent>
              <ErrorNotice error={documents.error} />
            </CardContent>
          ) : documents.isPending ? (
            <CardContent>
              <Skeleton className="h-10" />
            </CardContent>
          ) : items.length === 0 ? (
            <CardContent className="space-y-2 text-sm text-muted">
              <p>Nothing received yet. Send documents with an API key:</p>
              <pre className="overflow-x-auto rounded bg-canvas p-2 text-xs">
                curl -H "Authorization: Bearer idp_…" -F file=@invoice.pdf https://&lt;host&gt;/api/v1/documents
              </pre>
            </CardContent>
          ) : (
            <Table>
              <THead>
                <tr>
                  <TH>Document</TH>
                  <TH>Status</TH>
                  <TH>Received</TH>
                </tr>
              </THead>
              <tbody>
                {items.map((d) => (
                  <TR key={d.id}>
                    <TD>
                      <Link to={`/documents/${d.id}`} className="text-sm font-medium text-accent hover:underline">
                        {d.original_filename}
                      </Link>
                    </TD>
                    <TD>
                      <DocumentStatusBadge status={d.status} />
                    </TD>
                    <TD className="text-xs text-muted">{formatDateTime(d.received_at)}</TD>
                  </TR>
                ))}
              </tbody>
            </Table>
          )}
        </Card>
        {hasPermission(me, 'users:write') ? <ApiKeys /> : null}
      </div>
    </>
  )
}
