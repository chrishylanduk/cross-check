import { useState, useEffect } from 'react'
import { useRouter } from 'next/router'

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || 'http://localhost:8000'

interface SessionData {
  fileCount: number
  expiresAt: number
}

export default function SessionBanner() {
  const router = useRouter()
  const [session, setSession] = useState<SessionData | null>(null)

  useEffect(() => {
    const sessionId = sessionStorage.getItem('cross-check-session-id')
    const expiresAt = sessionStorage.getItem('cross-check-expires-at')
    if (!sessionId || !expiresAt) return

    const authToken = sessionStorage.getItem('prototype-auth-token')
    fetch(`${API_BASE}/api/collection`, {
      headers: {
        'X-Session-ID': sessionId,
        ...(authToken ? { 'X-Prototype-Auth': authToken } : {}),
      },
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data) setSession({ fileCount: data.file_count, expiresAt: Number(expiresAt) })
      })
      .catch(() => {})
  }, [])

  if (!session) return null

  const formatExpiry = (ts: number) =>
    new Date(ts * 1000).toLocaleString('en-GB', {
      weekday: 'long',
      day: 'numeric',
      month: 'long',
      hour: '2-digit',
      minute: '2-digit',
    })

  const handleStartAgain = () => {
    sessionStorage.removeItem('cross-check-session-id')
    sessionStorage.removeItem('cross-check-expires-at')
    router.push('/upload')
  }

  const fileWord = session.fileCount === 1 ? 'file' : 'files'

  return (
    <div style={{ background: '#f3f2f1', borderBottom: '1px solid #b1b4b6', padding: '8px 0' }}>
      <div className="govuk-width-container">
        <p className="govuk-body-s" style={{ margin: 0 }}>
          {session.fileCount} {fileWord} ready for analysis. Files will be deleted on{' '}
          <strong>{formatExpiry(session.expiresAt)}</strong>.{' '}
          <a href="/upload" className="govuk-link govuk-link--no-visited-state">
            View files
          </a>
          {' or '}
          <button
            type="button"
            onClick={handleStartAgain}
            className="govuk-link govuk-link--no-visited-state"
            style={{
              display: 'inline',
              border: 'none',
              background: 'none',
              cursor: 'pointer',
              padding: 0,
              font: 'inherit',
              lineHeight: 'inherit',
            }}
          >
            start again with new files
          </button>
          .
        </p>
      </div>
    </div>
  )
}
