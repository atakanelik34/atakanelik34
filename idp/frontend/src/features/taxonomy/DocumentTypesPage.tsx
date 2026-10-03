import { Plus } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router'

import { PageHeader } from '@/components/layout/PageHeader'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { ErrorNotice, Skeleton, Spinner } from '@/components/ui/feedback'
import { Input, Label } from '@/components/ui/input'
import { Table, TD, TH, THead, TR } from '@/components/ui/table'
import { hasPermission, useMe } from '@/features/auth/api'
import { useCreateFromTemplate, useCreateType, useDocumentTypes, useTemplates } from '@/features/taxonomy/api'

function NewTypeForm({ onDone }: { onDone: () => void }) {
  const create = useCreateType()
  const navigate = useNavigate()
  const [key, setKey] = useState('')
  const [name, setName] = useState('')

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    create.mutate(
      { key, name, description: '' },
      {
        onSuccess: (created) => {
          onDone()
          void navigate(`/document-types/${created.id}`)
        },
      },
    )
  }

  return (
    <form onSubmit={submit} className="space-y-3" noValidate>
      {create.isError ? <ErrorNotice error={create.error} /> : null}
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="type-name">Name</Label>
          <Input id="type-name" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="type-key">Key</Label>
          <Input
            id="type-key"
            value={key}
            placeholder="e.g. customs_declaration"
            onChange={(e) => setKey(e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, '_'))}
          />
        </div>
      </div>
      <div className="flex justify-end gap-2">
        <Button type="button" variant="secondary" onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit" disabled={!key || !name || create.isPending}>
          {create.isPending ? <Spinner /> : null}
          Create draft
        </Button>
      </div>
    </form>
  )
}

export function DocumentTypesPage() {
  const { data: me } = useMe()
  const canWrite = hasPermission(me, 'config:write')
  const types = useDocumentTypes()
  const templates = useTemplates()
  const fromTemplate = useCreateFromTemplate()
  const [creating, setCreating] = useState(false)
  const existing = new Set(types.data?.map((t) => t.key))

  return (
    <>
      <PageHeader
        title="Document types"
        description="The taxonomy that drives classification, extraction and validation. Schemas are versioned; published versions are immutable."
        actions={
          canWrite && !creating ? (
            <Button size="sm" onClick={() => setCreating(true)}>
              <Plus />
              New type
            </Button>
          ) : null
        }
      />
      {creating ? (
        <Card className="mb-6">
          <CardHeader>
            <CardTitle>New document type</CardTitle>
          </CardHeader>
          <CardContent>
            <NewTypeForm onDone={() => setCreating(false)} />
          </CardContent>
        </Card>
      ) : null}

      {types.isError ? (
        <ErrorNotice error={types.error} title="Unable to load document types" />
      ) : (
        <Card className="mb-6">
          <Table>
            <THead>
              <tr>
                <TH>Name</TH>
                <TH>Key</TH>
                <TH>Published</TH>
                <TH>Draft</TH>
                <TH>Status</TH>
              </tr>
            </THead>
            <tbody>
              {types.isPending ? (
                <TR>
                  <TD colSpan={5}>
                    <Skeleton className="h-5" />
                  </TD>
                </TR>
              ) : types.data.length === 0 ? (
                <TR>
                  <TD colSpan={5} className="py-8 text-center text-muted">
                    No document types yet. Without published types, documents stay unclassified and go to review.
                  </TD>
                </TR>
              ) : (
                types.data.map((t) => (
                  <TR key={t.id}>
                    <TD className="font-medium">
                      <Link to={`/document-types/${t.id}`} className="hover:text-accent">
                        {t.name}
                      </Link>
                    </TD>
                    <TD className="font-mono text-xs text-muted">{t.key}</TD>
                    <TD>{t.published_version ? <Badge tone="success">v{t.published_version}</Badge> : '—'}</TD>
                    <TD>{t.draft_version ? <Badge tone="warning">v{t.draft_version}</Badge> : '—'}</TD>
                    <TD>{t.is_active ? <Badge tone="accent">active</Badge> : <Badge>inactive</Badge>}</TD>
                  </TR>
                ))
              )}
            </tbody>
          </Table>
        </Card>
      )}

      {canWrite ? (
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Templates</CardTitle>
              <CardDescription>Starting points; they become your own type, editable like any other.</CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            {fromTemplate.isError ? (
              <div className="mb-3">
                <ErrorNotice error={fromTemplate.error} />
              </div>
            ) : null}
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {templates.data?.map((t) => (
                <div key={t.key} className="flex flex-col rounded-md border border-border p-3">
                  <p className="text-sm font-medium">{t.name}</p>
                  <p className="mt-0.5 flex-1 text-xs text-muted">
                    {t.description} · {t.field_count} fields
                  </p>
                  <Button
                    className="mt-3 self-start"
                    size="sm"
                    variant="secondary"
                    disabled={existing.has(t.key) || fromTemplate.isPending}
                    onClick={() => fromTemplate.mutate({ template_key: t.key, publish: true })}
                  >
                    {existing.has(t.key) ? 'Added' : 'Add & publish'}
                  </Button>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      ) : null}
    </>
  )
}
