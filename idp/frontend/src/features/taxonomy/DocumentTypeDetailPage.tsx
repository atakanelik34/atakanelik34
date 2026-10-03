import { ArrowLeft, Upload } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link, useParams } from 'react-router'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { ErrorNotice, Skeleton, Spinner } from '@/components/ui/feedback'
import { Table, TD, TH, THead, TR } from '@/components/ui/table'
import { hasPermission, useMe } from '@/features/auth/api'
import { useDocumentType, usePublish, useSaveDraft, useSchemaVersion, useUpdateType } from '@/features/taxonomy/api'
import { flatten, type SchemaDefinition, type SchemaVersion } from '@/features/taxonomy/types'
import { ApiError } from '@/lib/api'
import { formatDateTime } from '@/lib/format'

function FieldsTable({ definition }: { definition: SchemaDefinition }) {
  const rows = flatten(definition.fields)
  return (
    <Table>
      <THead>
        <tr>
          <TH>Path</TH>
          <TH>Type</TH>
          <TH>Required</TH>
          <TH className="text-right">Threshold</TH>
          <TH>Aliases</TH>
          <TH>Rules</TH>
        </tr>
      </THead>
      <tbody>
        {rows.map(({ path, field }) => (
          <TR key={path}>
            <TD className="font-mono text-xs">{path}</TD>
            <TD className="text-xs">{field.type}</TD>
            <TD>{field.required ? <Badge tone="accent">required</Badge> : null}</TD>
            <TD className="text-right font-mono text-xs">{Math.round(field.confidence_threshold * 100)}%</TD>
            <TD className="max-w-xs truncate text-xs text-muted">{field.aliases.join(', ')}</TD>
            <TD className="text-xs text-muted">{field.validation_rules.map((r) => r.type).join(', ')}</TD>
          </TR>
        ))}
      </tbody>
    </Table>
  )
}

function validationLines(error: unknown): string[] {
  if (!(error instanceof ApiError)) return []
  const errors = error.details.errors
  if (!Array.isArray(errors)) return []
  return errors.map((e: { loc?: string[]; msg?: string }) => `${(e.loc ?? []).join('.')}: ${e.msg ?? ''}`)
}

function SchemaEditor({
  typeId,
  version,
  canWrite,
}: {
  typeId: string
  version: SchemaVersion
  canWrite: boolean
}) {
  const saveDraft = useSaveDraft(typeId)
  const [text, setText] = useState(() => JSON.stringify(version.definition, null, 2))
  const [parseError, setParseError] = useState<string | null>(null)
  const isDraft = version.status === 'draft'

  const preview = useMemo<SchemaDefinition | null>(() => {
    try {
      return JSON.parse(text) as SchemaDefinition
    } catch {
      return null
    }
  }, [text])

  function save() {
    setParseError(null)
    if (!preview) {
      setParseError('The definition is not valid JSON.')
      return
    }
    saveDraft.mutate(preview)
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Fields · v{version.version}</CardTitle>
            <CardDescription>
              {preview
                ? `${flatten(preview.fields ?? []).length} paths · ${preview.rules?.length ?? 0} cross-field rules · ${preview.classification?.keywords?.length ?? 0} classification keywords`
                : 'Fix the JSON to see the preview'}
            </CardDescription>
          </div>
        </CardHeader>
        {preview ? <FieldsTable definition={preview} /> : null}
      </Card>

      <Card>
        <CardHeader>
          <div>
            <CardTitle>Definition</CardTitle>
            <CardDescription>
              {isDraft
                ? 'Draft: edit and save. The server validates field types, regexes and rules.'
                : 'Published and retired versions are read-only. Create a draft to change the schema.'}
            </CardDescription>
          </div>
          {isDraft && canWrite ? (
            <Button size="sm" onClick={save} disabled={saveDraft.isPending}>
              {saveDraft.isPending ? <Spinner /> : null}
              Save draft
            </Button>
          ) : null}
        </CardHeader>
        <CardContent className="space-y-3">
          {parseError ? <ErrorNotice error={new Error(parseError)} /> : null}
          {saveDraft.isError ? (
            <div role="alert" className="rounded-md border border-danger/20 bg-danger-soft px-4 py-3 text-xs text-danger">
              <p className="font-medium">{saveDraft.error.message}</p>
              <ul className="mt-1 list-disc pl-4 font-mono">
                {validationLines(saveDraft.error).map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            </div>
          ) : null}
          {saveDraft.isSuccess ? <p className="text-xs text-success">Draft saved.</p> : null}
          <textarea
            aria-label="Schema definition JSON"
            className="h-[420px] w-full rounded-md border border-border bg-canvas/50 p-3 font-mono text-xs leading-relaxed focus-visible:border-accent focus-visible:outline-none"
            value={text}
            readOnly={!isDraft || !canWrite}
            spellCheck={false}
            onChange={(e) => setText(e.target.value)}
          />
        </CardContent>
      </Card>
    </div>
  )
}

export function DocumentTypeDetailPage() {
  const { id = '' } = useParams()
  const { data: me } = useMe()
  const canWrite = hasPermission(me, 'config:write')
  const type = useDocumentType(id)
  const versions = type.data?.versions ?? []
  const draft = versions.find((v) => v.status === 'draft')
  const published = versions.find((v) => v.status === 'published')
  const [selected, setSelected] = useState<number | null>(null)
  const shown = selected ?? draft?.version ?? published?.version ?? null
  const version = useSchemaVersion(id, shown)
  const saveDraft = useSaveDraft(id)
  const publish = usePublish(id)
  const update = useUpdateType(id)

  if (type.isError) return <ErrorNotice error={type.error} title="Unable to load document type" />

  return (
    <>
      <Link to="/document-types" className="mb-3 inline-flex items-center gap-1 text-xs text-muted hover:text-ink">
        <ArrowLeft aria-hidden className="size-3.5" />
        Document types
      </Link>
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        {type.data ? (
          <div>
            <h1 className="text-lg font-semibold tracking-tight">{type.data.name}</h1>
            <p className="mt-1 font-mono text-xs text-muted">{type.data.key}</p>
          </div>
        ) : (
          <Skeleton className="h-10 w-64" />
        )}
        {canWrite && type.data ? (
          <div className="flex gap-2">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => update.mutate({ is_active: !type.data.is_active })}
            >
              {type.data.is_active ? 'Deactivate' : 'Activate'}
            </Button>
            {!draft ? (
              <Button variant="secondary" size="sm" onClick={() => saveDraft.mutate(null)}>
                New draft
              </Button>
            ) : null}
            <Button size="sm" disabled={!draft || publish.isPending} onClick={() => publish.mutate()}>
              {publish.isPending ? <Spinner /> : <Upload />}
              Publish draft
            </Button>
          </div>
        ) : null}
      </div>
      {publish.isError ? (
        <div className="mb-4">
          <ErrorNotice error={publish.error} />
        </div>
      ) : null}

      <div className="grid gap-6 lg:grid-cols-4">
        <Card className="lg:col-span-1">
          <CardHeader>
            <CardTitle>Versions</CardTitle>
          </CardHeader>
          <ul className="divide-y divide-border">
            {versions.map((v) => (
              <li key={v.id}>
                <button
                  type="button"
                  onClick={() => setSelected(v.version)}
                  className={`flex w-full items-center justify-between px-5 py-2.5 text-left text-sm hover:bg-canvas ${
                    v.version === shown ? 'bg-accent-soft' : ''
                  }`}
                >
                  <span className="font-medium">v{v.version}</span>
                  <Badge tone={v.status === 'published' ? 'success' : v.status === 'draft' ? 'warning' : 'neutral'}>
                    {v.status}
                  </Badge>
                </button>
                <p className="px-5 pb-2 text-[11px] text-muted">
                  {v.published_at ? `published ${formatDateTime(v.published_at)}` : `created ${formatDateTime(v.created_at)}`}
                </p>
              </li>
            ))}
          </ul>
        </Card>

        <div className="lg:col-span-3">
          {version.data ? (
            <SchemaEditor
              key={version.data.id}
              typeId={id}
              version={version.data}
              canWrite={canWrite}
            />
          ) : (
            <Skeleton className="h-96" />
          )}
        </div>
      </div>
    </>
  )
}
