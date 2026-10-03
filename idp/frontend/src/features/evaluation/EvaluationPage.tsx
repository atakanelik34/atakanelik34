import { useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router'

import { PageHeader } from '@/components/layout/PageHeader'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { ErrorNotice, Skeleton, Spinner } from '@/components/ui/feedback'
import { Input, Label, Select } from '@/components/ui/input'
import { Table, TD, TH, THead, TR } from '@/components/ui/table'
import { hasPermission, useMe } from '@/features/auth/api'
import { useCreateDataset, useDatasets } from '@/features/evaluation/api'
import { pct } from '@/features/evaluation/format'
import { useDocumentTypes } from '@/features/taxonomy/api'
import { formatDateTime } from '@/lib/format'

function CreateDataset() {
  const navigate = useNavigate()
  const types = useDocumentTypes()
  const create = useCreateDataset()
  const [name, setName] = useState('')
  const [docType, setDocType] = useState('')

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    create.mutate(
      { name, description: '', document_type: docType || null },
      { onSuccess: (dataset) => navigate(`/evaluation/${dataset.id}`) },
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>New dataset</CardTitle>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} className="flex flex-wrap items-end gap-3">
          <div className="space-y-1">
            <Label htmlFor="dataset-name">Name</Label>
            <Input id="dataset-name" value={name} onChange={(e) => setName(e.target.value)} required maxLength={120} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="dataset-type">Document type</Label>
            <Select id="dataset-type" value={docType} onChange={(e) => setDocType(e.target.value)} className="w-48">
              <option value="">Any</option>
              {types.data?.map((t) => (
                <option key={t.id} value={t.key}>
                  {t.name}
                </option>
              ))}
            </Select>
          </div>
          <Button type="submit" disabled={create.isPending || !name.trim()}>
            {create.isPending ? <Spinner /> : null}
            Create
          </Button>
        </form>
        {create.isError ? (
          <div className="mt-3">
            <ErrorNotice error={create.error} />
          </div>
        ) : null}
      </CardContent>
    </Card>
  )
}

export function EvaluationPage() {
  const { data: me } = useMe()
  const datasets = useDatasets()
  return (
    <>
      <PageHeader
        title="Evaluation"
        description="Measure extraction against ground truth from approved reviews: precision, recall, calibration, intervention rate and cost."
      />
      <div className="space-y-6">
        {hasPermission(me, 'config:write') ? <CreateDataset /> : null}
        {datasets.isError ? (
          <ErrorNotice error={datasets.error} title="Unable to load datasets" />
        ) : (
          <Card>
            <Table>
              <THead>
                <tr>
                  <TH>Dataset</TH>
                  <TH>Type</TH>
                  <TH>Items</TH>
                  <TH>Last F1</TH>
                  <TH>Intervention</TH>
                  <TH>Last run</TH>
                </tr>
              </THead>
              <tbody>
                {datasets.isPending ? (
                  <TR>
                    <TD colSpan={6}>
                      <Skeleton className="h-5" />
                    </TD>
                  </TR>
                ) : datasets.data.length === 0 ? (
                  <TR>
                    <TD colSpan={6} className="text-muted">
                      No datasets yet. Create one, then import approved reviews as ground truth.
                    </TD>
                  </TR>
                ) : (
                  datasets.data.map((d) => (
                    <TR key={d.id}>
                      <TD>
                        <Link to={`/evaluation/${d.id}`} className="font-medium text-accent hover:underline">
                          {d.name}
                        </Link>
                      </TD>
                      <TD className="text-xs">{d.document_type ?? 'any'}</TD>
                      <TD className="text-xs">{d.items}</TD>
                      <TD className="text-xs">{pct(d.last_run?.metrics.f1)}</TD>
                      <TD className="text-xs">{pct(d.last_run?.metrics.intervention_rate)}</TD>
                      <TD className="text-xs text-muted">{d.last_run ? formatDateTime(d.last_run.created_at) : 'never'}</TD>
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
