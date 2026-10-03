import { ArrowLeft, Upload } from 'lucide-react'
import { useState, type ChangeEvent, type FormEvent } from 'react'
import { Link, useParams } from 'react-router'

import { PageHeader } from '@/components/layout/PageHeader'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { ErrorNotice, Skeleton, Spinner } from '@/components/ui/feedback'
import { Input, Label } from '@/components/ui/input'
import { Table, TD, TH, THead, TR } from '@/components/ui/table'
import { hasPermission, useMe } from '@/features/auth/api'
import { useConnection, useImportRecords, useRecords, useTestLookup, useUpdateConnection } from '@/features/enrichment/api'
import { KIND_LABEL } from '@/features/enrichment/ConnectionsPage'

function Records({ id }: { id: string }) {
  const [q, setQ] = useState('')
  const records = useRecords(id, q)
  const columns = Array.from(new Set(records.data?.items.flatMap((r) => Object.keys(r.attributes)) ?? [])).slice(0, 5)
  return (
    <Card>
      <CardHeader>
        <CardTitle>Records{records.data ? ` · ${records.data.total}` : ''}</CardTitle>
        <Input aria-label="Search records" placeholder="Search by name" className="w-56" value={q} onChange={(e) => setQ(e.target.value)} />
      </CardHeader>
      <Table>
        <THead>
          <tr>
            <TH>Key</TH>
            <TH>Name</TH>
            {columns.map((c) => (
              <TH key={c}>{c.replaceAll('_', ' ')}</TH>
            ))}
          </tr>
        </THead>
        <tbody>
          {records.data?.items.map((r) => (
            <TR key={`${r.entity}:${r.key}`}>
              <TD className="font-mono text-xs">{r.key}</TD>
              <TD className="text-xs">{r.name}</TD>
              {columns.map((c) => (
                <TD key={c} className="text-xs">
                  {r.attributes[c] ?? '—'}
                </TD>
              ))}
            </TR>
          ))}
        </tbody>
      </Table>
    </Card>
  )
}

function ImportCard({ id }: { id: string }) {
  const importRecords = useImportRecords(id)
  function onFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    if (file) importRecords.mutate(file)
    event.target.value = ''
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle>Import vendors (CSV)</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        <p className="text-xs text-muted">
          Columns: <code>key</code>, <code>name</code>, optional <code>tax_id</code>, <code>iban</code>; other columns become attributes. An import replaces all vendor records of
          this connection.
        </p>
        <label className="inline-flex cursor-pointer items-center gap-2 rounded-md border border-border px-3 py-1.5 text-sm hover:bg-canvas">
          {importRecords.isPending ? <Spinner /> : <Upload className="size-4" />}
          Choose CSV file
          <input type="file" accept=".csv,text/csv" className="sr-only" onChange={onFile} />
        </label>
        {importRecords.data ? (
          <output className="block text-xs">
            {importRecords.data.imported} imported, {importRecords.data.skipped} skipped
            {importRecords.data.errors.length > 0 ? `: ${importRecords.data.errors.join('; ')}` : ''}
          </output>
        ) : null}
        {importRecords.isError ? <ErrorNotice error={importRecords.error} /> : null}
      </CardContent>
    </Card>
  )
}

function TestLookup({ id }: { id: string }) {
  const test = useTestLookup(id)
  const [name, setName] = useState('')
  const [taxId, setTaxId] = useState('')
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const criteria: Record<string, string> = {}
    if (name) criteria.name = name
    if (taxId) criteria.tax_id = taxId
    test.mutate(criteria)
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle>Test lookup</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <form onSubmit={submit} className="flex flex-wrap items-end gap-3">
          <div className="space-y-1">
            <Label htmlFor="lookup-name">Vendor name</Label>
            <Input id="lookup-name" value={name} onChange={(e) => setName(e.target.value)} className="w-56" />
          </div>
          <div className="space-y-1">
            <Label htmlFor="lookup-tax">Tax id</Label>
            <Input id="lookup-tax" value={taxId} onChange={(e) => setTaxId(e.target.value)} className="w-44" />
          </div>
          <Button type="submit" size="sm" variant="secondary" disabled={test.isPending || (!name && !taxId)}>
            {test.isPending ? <Spinner /> : null}
            Look up
          </Button>
        </form>
        {test.isError ? <ErrorNotice error={test.error} /> : null}
        {test.data ? (
          <div className="text-sm">
            <Badge tone={test.data.status === 'matched' ? 'success' : 'warning'}>{test.data.status.replace('_', ' ')}</Badge>
            <ul className="mt-2 space-y-1 text-xs">
              {test.data.candidates.map((c) => (
                <li key={c.key}>
                  <span className="font-mono">{c.key}</span> {c.name} · {Math.round(c.score * 100)}% ({c.matched_on.join(', ')})
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </CardContent>
    </Card>
  )
}

export function ConnectionDetailPage() {
  const { id = '' } = useParams()
  const { data: me } = useMe()
  const connection = useConnection(id)
  const update = useUpdateConnection(id)
  if (connection.isError) return <ErrorNotice error={connection.error} title="Unable to load connection" />
  if (!connection.data) return <Skeleton className="h-64" />
  const c = connection.data
  const canWrite = hasPermission(me, 'config:write')

  return (
    <>
      <PageHeader
        title={c.name}
        description={`${KIND_LABEL[c.kind]} · key ${c.key}`}
        actions={
          <div className="flex items-center gap-2">
            <Link to="/connections" className="text-muted hover:text-ink" aria-label="Back to connections">
              <ArrowLeft className="size-4" />
            </Link>
            {canWrite ? (
              <Button variant="secondary" size="sm" disabled={update.isPending} onClick={() => update.mutate({ is_active: !c.is_active })}>
                {c.is_active ? 'Deactivate' : 'Activate'}
              </Button>
            ) : null}
          </div>
        }
      />
      <div className="space-y-6">
        {c.kind === 'rest' ? (
          <Card>
            <CardHeader>
              <CardTitle>Configuration</CardTitle>
            </CardHeader>
            <pre className="overflow-x-auto px-4 pb-4 text-xs">{JSON.stringify(c.config, null, 2)}</pre>
          </Card>
        ) : null}
        {c.kind === 'master_data' && canWrite ? <ImportCard id={id} /> : null}
        {canWrite ? <TestLookup id={id} /> : null}
        {c.kind === 'master_data' ? <Records id={id} /> : null}
      </div>
    </>
  )
}
