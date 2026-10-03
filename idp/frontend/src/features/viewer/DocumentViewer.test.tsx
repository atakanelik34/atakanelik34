import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { DocumentViewer, type Highlight } from '@/features/viewer/DocumentViewer'
import { json, renderRoutes } from '@/test/render'

function Harness({ highlights }: { highlights: Highlight[] }) {
  const [page, setPage] = useState(1)
  return <DocumentViewer documentId="d1" pageCount={2} page={page} onPageChange={setPage} highlights={highlights} />
}

describe('DocumentViewer', () => {
  it('renders the page with a selectable text layer and highlights on the right page', async () => {
    Element.prototype.scrollIntoView = vi.fn<() => void>()
    vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
      const url = String(input)
      const page = url.includes('/pages/2/') ? 2 : 1
      if (url.endsWith('/image')) {
        return Promise.resolve(json({ page_number: page, url: `https://store/p${page}.webp`, width: 850, height: 1100, expires_at: '' }))
      }
      return Promise.resolve(
        json({
          page_number: page,
          source: 'native',
          text_quality: 1,
          language: 'en',
          ocr_confidence: null,
          lines: [{ id: `p${page}-l0`, text: `Total page ${page}`, bbox: [0.1, 0.1, 0.4, 0.12], words: [{ text: `Total${page}`, bbox: [0.1, 0.1, 0.2, 0.12], confidence: null }] }],
        }),
      )
    })
    renderRoutes([{ path: '/', element: <Harness highlights={[{ page: 2, bbox: [0.5, 0.5, 0.6, 0.55], label: 'total' }]} /> }], '/')

    expect(await screen.findByAltText('Page 1')).toHaveAttribute('src', 'https://store/p1.webp')
    expect(await screen.findByText('Total1')).toBeInTheDocument()
    expect(screen.queryByTestId('viewer-highlight')).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Next page' }))
    expect(await screen.findByAltText('Page 2')).toBeInTheDocument()
    const highlight = await screen.findByTestId('viewer-highlight')
    expect(highlight).toHaveStyle({ left: '50%', top: '50%' })
    expect(screen.getByText('Page 2 / 2')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Next page' })).toBeDisabled()

    await userEvent.click(screen.getByRole('button', { name: 'Zoom in' }))
    expect(screen.getByText('125%')).toBeInTheDocument()
  })
})
