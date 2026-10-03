import { FileUp } from 'lucide-react'
import { useRef, useState, type DragEvent } from 'react'
import { Link } from 'react-router'

import { Spinner } from '@/components/ui/feedback'
import { useUploadDocument } from '@/features/documents/api'
import { ApiError } from '@/lib/api'
import { cn } from '@/lib/utils'

const ACCEPT = 'application/pdf,image/png,image/jpeg,image/tiff'

interface UploadOutcome {
  name: string
  documentId?: string
  error?: string
  duplicateOf?: string
}

function describe(error: unknown): Pick<UploadOutcome, 'error' | 'duplicateOf'> {
  if (error instanceof ApiError) {
    const existing = error.details.existing_document_id
    if (error.code === 'duplicate_document' && typeof existing === 'string') {
      return { error: 'Already uploaded', duplicateOf: existing }
    }
    return { error: error.message }
  }
  return { error: 'Upload failed' }
}

export function UploadPanel() {
  const upload = useUploadDocument()
  const input = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const [busy, setBusy] = useState(false)
  const [outcomes, setOutcomes] = useState<UploadOutcome[]>([])

  async function handleFiles(list: FileList | null) {
    if (!list || list.length === 0) return
    setBusy(true)
    const results: UploadOutcome[] = []
    // Sequential: keeps server load predictable and results in order.
    for (const file of Array.from(list)) {
      try {
        const res = await upload.mutateAsync(file)
        results.push({ name: file.name, documentId: res.document.id })
      } catch (error) {
        results.push({ name: file.name, ...describe(error) })
      }
      setOutcomes([...results])
    }
    setBusy(false)
    if (input.current) input.current.value = ''
  }

  function onDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault()
    setDragging(false)
    void handleFiles(event.dataTransfer.files)
  }

  return (
    <div className="space-y-3">
      {/* Drag-and-drop enhances the label; the file input remains the keyboard path. */}
      {/* oxlint-disable-next-line jsx-a11y/no-noninteractive-element-interactions */}
      <label
        htmlFor="document-upload"
        onDragOver={(e) => {
          e.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={cn(
          'flex cursor-pointer items-center gap-4 rounded-lg border border-dashed border-border bg-surface px-5 py-4 transition-colors',
          dragging ? 'border-accent bg-accent-soft' : 'hover:border-accent/50',
        )}
      >
        <span className="flex size-10 items-center justify-center rounded-md bg-accent-soft text-accent">
          {busy ? <Spinner /> : <FileUp aria-hidden className="size-5" />}
        </span>
        <span className="flex-1">
          <span className="block text-sm font-medium">Upload documents</span>
          <span className="block text-xs text-muted">
            Drop files here or click to browse · PDF, PNG, JPEG, TIFF · processed asynchronously
          </span>
        </span>
        <input
          ref={input}
          id="document-upload"
          type="file"
          accept={ACCEPT}
          multiple
          className="sr-only"
          disabled={busy}
          onChange={(e) => void handleFiles(e.target.files)}
        />
      </label>
      {outcomes.length > 0 ? (
        <ul className="space-y-1 text-xs" aria-live="polite">
          {outcomes.map((o, i) => (
            <li key={`${o.name}-${i}`} className="flex items-center gap-2">
              <span className="truncate font-medium">{o.name}</span>
              {o.documentId ? (
                <Link to={`/documents/${o.documentId}`} className="text-accent hover:underline">
                  queued
                </Link>
              ) : (
                <span className="text-danger">
                  {o.error}
                  {o.duplicateOf ? (
                    <>
                      {' · '}
                      <Link to={`/documents/${o.duplicateOf}`} className="text-accent hover:underline">
                        open existing
                      </Link>
                    </>
                  ) : null}
                </span>
              )}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}
