import { ChevronLeft, ChevronRight, RotateCw, Type, ZoomIn, ZoomOut } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { ErrorNotice, Skeleton } from '@/components/ui/feedback'
import { usePageImage, usePageLayout, type BBox } from '@/features/viewer/api'
import { cn } from '@/lib/utils'

export interface Highlight {
  page: number
  bbox: BBox
  label?: string
}

const ZOOM_STEPS = [0.5, 0.75, 1, 1.25, 1.5, 2, 3]

function boxStyle([x0, y0, x1, y1]: BBox) {
  return {
    left: `${x0 * 100}%`,
    top: `${y0 * 100}%`,
    width: `${(x1 - x0) * 100}%`,
    height: `${(y1 - y0) * 100}%`,
  }
}

/**
 * Page viewer: rendered page image with an invisible, selectable text layer and
 * bounding-box highlights. Coordinates are normalised (0..1, origin top-left),
 * so overlays track zoom and rotation without recomputation.
 */
export function DocumentViewer({
  documentId,
  pageCount,
  page,
  onPageChange,
  highlights = [],
  className,
}: {
  documentId: string
  pageCount: number
  page: number
  onPageChange: (page: number) => void
  highlights?: Highlight[]
  className?: string
}) {
  const [zoomIndex, setZoomIndex] = useState(2)
  const [rotation, setRotation] = useState(0)
  const [showText, setShowText] = useState(false)
  const image = usePageImage(documentId, page)
  const layout = usePageLayout(documentId, page, true)
  const highlightRef = useRef<HTMLDivElement>(null)
  const zoom = ZOOM_STEPS[zoomIndex] ?? 1
  const pageHighlights = highlights.filter((h) => h.page === page)

  const focusKey = pageHighlights[0] ? `${page}:${pageHighlights[0].bbox.join(',')}` : ''
  useEffect(() => {
    if (!focusKey) return
    highlightRef.current?.scrollIntoView({ block: 'center', inline: 'center', behavior: 'smooth' })
  }, [focusKey])

  const sideways = rotation % 180 !== 0
  const aspect = image.data ? image.data.width / Math.max(image.data.height, 1) : 0.77

  return (
    <div className={cn('flex min-h-0 flex-col', className)}>
      <div className="flex items-center gap-1 border-b border-border px-3 py-2">
        <Button
          variant="ghost"
          size="icon"
          aria-label="Previous page"
          disabled={page <= 1}
          onClick={() => onPageChange(page - 1)}
        >
          <ChevronLeft />
        </Button>
        <span className="min-w-20 text-center text-xs tabular-nums" aria-live="polite">
          Page {page} / {pageCount}
        </span>
        <Button
          variant="ghost"
          size="icon"
          aria-label="Next page"
          disabled={page >= pageCount}
          onClick={() => onPageChange(page + 1)}
        >
          <ChevronRight />
        </Button>
        <span className="mx-2 h-5 w-px bg-border" />
        <Button
          variant="ghost"
          size="icon"
          aria-label="Zoom out"
          disabled={zoomIndex === 0}
          onClick={() => setZoomIndex((z) => z - 1)}
        >
          <ZoomOut />
        </Button>
        <span className="w-12 text-center text-xs tabular-nums">{Math.round(zoom * 100)}%</span>
        <Button
          variant="ghost"
          size="icon"
          aria-label="Zoom in"
          disabled={zoomIndex === ZOOM_STEPS.length - 1}
          onClick={() => setZoomIndex((z) => z + 1)}
        >
          <ZoomIn />
        </Button>
        <Button variant="ghost" size="icon" aria-label="Rotate" onClick={() => setRotation((r) => (r + 90) % 360)}>
          <RotateCw />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          aria-label="Show text layer"
          aria-pressed={showText}
          onClick={() => setShowText((s) => !s)}
          className={showText ? 'bg-accent-soft text-accent' : undefined}
        >
          <Type />
        </Button>
        {layout.data ? (
          <span className="ml-auto text-[11px] text-muted">
            {layout.data.source === 'none' ? 'no readable text' : `${layout.data.source} text`}
            {layout.data.ocr_confidence !== null
              ? ` · OCR ${Math.round(layout.data.ocr_confidence * 100)}%`
              : ''}
          </span>
        ) : null}
      </div>
      <div className="min-h-0 flex-1 overflow-auto bg-canvas p-4">
        {image.isError ? (
          <ErrorNotice error={image.error} title="Page image unavailable" />
        ) : !image.data ? (
          <Skeleton className="mx-auto aspect-[3/4] w-full max-w-xl" />
        ) : (
          <div
            className="mx-auto"
            style={{
              width: `${zoom * (sideways ? 100 / aspect : 100)}%`,
              maxWidth: sideways ? undefined : `${image.data.width * zoom * 1.5}px`,
            }}
          >
            <div
              className="relative origin-center bg-white shadow-sm ring-1 ring-border"
              style={{
                aspectRatio: `${aspect}`,
                transform: rotation ? `rotate(${rotation}deg)` : undefined,
                // Lets word font sizes use cqh (percent of page height).
                containerType: 'size',
              }}
              data-testid="viewer-page"
            >
              <img
                src={image.data.url}
                alt={`Page ${page}`}
                className="absolute inset-0 h-full w-full select-none"
                draggable={false}
              />
              {layout.data ? (
                <div className="absolute inset-0" aria-hidden={!showText}>
                  {layout.data.lines.flatMap((line) =>
                    line.words.map((word, i) => (
                      <span
                        key={`${line.id}-${i}`}
                        className={cn(
                          'absolute leading-none whitespace-pre',
                          showText ? 'bg-accent/10 text-transparent' : 'text-transparent',
                        )}
                        style={{ ...boxStyle(word.bbox), fontSize: `${(word.bbox[3] - word.bbox[1]) * 100}cqh` }}
                      >
                        {word.text}{' '}
                      </span>
                    )),
                  )}
                </div>
              ) : null}
              {pageHighlights.map((h, i) => (
                <div
                  key={`${h.bbox.join(',')}-${i}`}
                  ref={i === 0 ? highlightRef : undefined}
                  className="pointer-events-none absolute rounded-[2px] bg-accent/15 ring-2 ring-accent"
                  style={boxStyle(h.bbox)}
                  data-testid="viewer-highlight"
                  title={h.label}
                />
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
