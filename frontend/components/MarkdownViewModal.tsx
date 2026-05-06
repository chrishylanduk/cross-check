import { useEffect, useRef } from 'react'

interface MarkdownViewModalProps {
  filename: string
  content: string | null
  loading: boolean
  error: string | null
  onClose: () => void
}

export default function MarkdownViewModal({
  filename,
  content,
  loading,
  error,
  onClose,
}: MarkdownViewModalProps) {
  const closeButtonRef = useRef<HTMLButtonElement>(null)
  const dialogRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    closeButtonRef.current?.focus()
  }, [])

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [onClose])

  // Prevent background scroll
  useEffect(() => {
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = '' }
  }, [])

  return (
    <>
      <div
        style={{
          position: 'fixed',
          inset: 0,
          background: 'rgba(0,0,0,0.5)',
          zIndex: 1000,
        }}
        aria-hidden="true"
        onClick={onClose}
      />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="markdown-modal-title"
        style={{
          position: 'fixed',
          top: '50%',
          left: '50%',
          transform: 'translate(-50%, -50%)',
          zIndex: 1001,
          background: '#fff',
          width: 'min(90vw, 900px)',
          maxHeight: '80vh',
          display: 'flex',
          flexDirection: 'column',
          boxShadow: '0 4px 16px rgba(0,0,0,0.3)',
        }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'flex-start',
            justifyContent: 'space-between',
            padding: '20px 20px 16px',
            borderBottom: '1px solid #b1b4b6',
          }}
        >
          <h2
            id="markdown-modal-title"
            className="govuk-heading-m"
            style={{ margin: 0, wordBreak: 'break-all' }}
          >
            {filename}
          </h2>
          <button
            ref={closeButtonRef}
            type="button"
            className="govuk-button govuk-button--secondary"
            style={{ marginLeft: '16px', flexShrink: 0, marginBottom: 0 }}
            onClick={onClose}
            aria-label="Close"
          >
            Close
          </button>
        </div>

        <div style={{ padding: '20px', overflowY: 'auto', flex: 1 }}>
          {loading && <p className="govuk-body">Loading…</p>}
          {error && (
            <div className="govuk-error-summary">
              <div role="alert">
                <h2 className="govuk-error-summary__title">There is a problem</h2>
                <div className="govuk-error-summary__body">
                  <p>{error}</p>
                </div>
              </div>
            </div>
          )}
          {content !== null && !loading && (
            <>
              <p className="govuk-body-s govuk-!-colour-secondary">
                This is the plain text extracted from your file. It is what Cross-check analyses.
              </p>
              <pre
                style={{
                  fontFamily: 'monospace',
                  fontSize: '14px',
                  lineHeight: '1.5',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                  background: '#f3f2f1',
                  padding: '16px',
                  margin: 0,
                  border: '1px solid #b1b4b6',
                }}
              >
                {content}
              </pre>
            </>
          )}
        </div>
      </div>
    </>
  )
}
