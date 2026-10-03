import { useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router'

import { PageHeader } from '@/components/layout/PageHeader'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { ErrorNotice, Skeleton, Spinner } from '@/components/ui/feedback'
import { Input, Label, Select } from '@/components/ui/input'
import { Table, TD, TH, THead, TR } from '@/components/ui/table'
import { hasPermission, useMe } from '@/features/auth/api'
import { useConnections, useCreateConnection, type ConnectionKind } from '@/features/enrichment/api'

export const KIND_LABEL: Record<ConnectionKind, string> = {
  master_data: 'Master data (CSV)',
  rest: 'REST API',
  mock_erp: 'Mock ERP (demo)',
}

const REST_EXAMPLE = JSON.stringify(
  { base_url: 'https://erp.internal/api', path: '/vendors', query: { tax_id: 'vatId', name: 'q' }, results_path: 'items', key_field: 'id', name_field: 'name' },
  null,
  2,
)

function CreateConnection() {
  const navigate = useNavigate()
  const create = useCreateConnection()
  const [key, setKey] = useState('vendors')
  const [name, setName] = useState('Vendor master')
  const [kind, setKind] = useState<ConnectionKind>('master_data')
  const [config, setConfig] = useState(REST_EXAMPLE)
  const [configError, setConfigError] = useState<string | null>(null)

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    let parsed: Record<string, unknown> = {}
    if (kind === 'rest') {
      try {
        parsed = JSON.parse(config) as Record<string, unknown>
      } catch {
        setConfigError('Configuration must be valid JSON')
        return
      }
    }
    setConfigError(null)
    create.mutate({ key, name, kind, config: parsed }, { onSuccess: (c) => navigate(`/connections/${c.id}`) })
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>New connection</CardTitle>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} className="space-y-3">
          <div className="flex flex-wrap items-end gap-3">
            <div className="space-y-1">
              <Label htmlFor="conn-key">Key</Label>
              <Input id="conn-key" value={key} onChange={(e) => setKey(e.target.value)} className="w-40" required />
            </div>
            <div className="space-y-1">
              <Label htmlFor="conn-name">Name</Label>
              <Input id="conn-name" value={name} onChange={(e) => setName(e.target.value)} className="w-56" required />
            </div>
            <div className="space-y-1">
              <Label htmlFor="conn-kind">Kind</Label>
              <Select id="conn-kind" value={kind} onChange={(e) => setKind(e.target.value as ConnectionKind)} className="w-48">
                {Object.entries(KIND_LABEL).map(([k, label]) => (
                  <option key={k} value={k}>
                    {label}
                  </option>
                ))}
              </Select>
            </div>
            <Button type="submit" disabled={create.isPending}>
              {create.isPending ? <Spinner /> : null}
              Create
            </Button>
          </div>
          {kind === 'rest' ? (
            <div className="space-y-1">
              <Label htmlFor="conn-config">Configuration (JSON). Credentials: set `auth_header` and `secret_env` (an IDP_SECRET_* variable) — never the secret itself.</Label>
              <textarea id="conn-config" className="h-40 w-full rounded-md border border-border p-2 font-mono text-xs" value={config} onChange={(e) => setConfig(e.target.value)} />
            </div>
          ) : null}
          {kind === 'mock_erp' ? (
            <p className="text-xs text-warning">Mock connections serve a fixed, fictitious demo vendor list and are refused unless mock providers are allowed.</p>
          ) : null}
          {configError ? <p className="text-xs text-danger">{configError}</p> : null}
          {create.isError ? <ErrorNotice error={create.error} /> : null}
        </form>
      </CardContent>
    </Card>
  )
}

export function ConnectionsPage() {
  const { data: me } = useMe()
  const connections = useConnections()
  return (
    <>
      <PageHeader
        title="Connections"
        description="Master data and systems used to enrich documents (vendor lookup). Document types reference connections by key."
      />
      <div className="space-y-6">
        {hasPermission(me, 'config:write') ? <CreateConnection /> : null}
        {connections.isError ? (
          <ErrorNotice error={connections.error} title="Unable to load connections" />
        ) : (
          <Card>
            <Table>
              <THead>
                <tr>
                  <TH>Connection</TH>
                  <TH>Kind</TH>
                  <TH>Records</TH>
                  <TH>Status</TH>
                </tr>
              </THead>
              <tbody>
                {connections.isPending ? (
                  <TR>
                    <TD colSpan={4}>
                      <Skeleton className="h-5" />
                    </TD>
                  </TR>
                ) : connections.data.length === 0 ? (
                  <TR>
                    <TD colSpan={4} className="text-muted">
                      No connections yet. Invoices look vendors up in a connection with the key “vendors”.
                    </TD>
                  </TR>
                ) : (
                  connections.data.map((c) => (
                    <TR key={c.id}>
                      <TD>
                        <Link to={`/connections/${c.id}`} className="font-medium text-accent hover:underline">
                          {c.name}
                        </Link>
                        <p className="font-mono text-[11px] text-muted">{c.key}</p>
                      </TD>
                      <TD className="text-xs">
                        {KIND_LABEL[c.kind]}
                        {c.is_mock ? (
                          <Badge tone="warning" className="ml-2">
                            mock
                          </Badge>
                        ) : null}
                      </TD>
                      <TD className="text-xs">{c.kind === 'master_data' ? c.records : '—'}</TD>
                      <TD>
                        <Badge tone={c.is_active ? 'success' : 'neutral'}>{c.is_active ? 'active' : 'inactive'}</Badge>
                      </TD>
                    </TR>
                  ))
                )}
              </tbody>
            </Table>
          </Card>
        )}
      </div>
    </>
  )
}
