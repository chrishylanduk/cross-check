import { useState, useEffect } from 'react'
import { useRouter } from 'next/router'

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || 'http://localhost:8000'

interface SessionData {
  fileCount: number
  issueCount: number | null
  expiresAt: number
  finalised: boolean
}

export default function SessionBanner() {
  const router = useRouter()
  const [session, setSession] = useState<SessionData | null>(null)

  useEffect(() => {
    const sessionId = sessionStorage.getItem('cross-check-session-id')
    const expiresAt = sessionStorage.getItem('cross-check-expires-at')
    if (!sessionId || !expiresAt) return

    const headers = {
      'X-Session-ID': sessionId,
      ...(sessionStorage.getItem('prototype-auth-token')
        ? { 'X-Prototype-Auth': sessionStorage.getItem('prototype-auth-token') as string }
        : {}),
    }

    Promise.all([
      fetch(`${API_BASE}/api/collection`, { headers }).then((r) => (r.ok ? r.json() : null)),
      fetch(`${API_BASE}/api/issues`, { headers }).then((r) => (r.ok ? r.json() : null)),
    ]).then(([collection, issues]) => {
      if (!collection) return
      const issueCount = issues
        ? Object.values(issues.modules as Record<string, { issue_count: number }>).reduce(
            (sum, m) => sum + m.issue_count,
            0,
          )
        : 0
      setSession({
        fileCount: collection.file_count,
        issueCount: collection.finalised ? issueCount : null,
        expiresAt: Number(expiresAt),
        finalised: collection.finalised,
      })
    }).catch(() => {})
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

  const linkStyle = {
    display: 'inline' as const,
    border: 'none',
    background: 'none',
    cursor: 'pointer',
    padding: 0,
    font: 'inherit',
    lineHeight: 'inherit',
  }

  return (
    <div style={{ background: '#f3f2f1', borderBottom: '1px solid #b1b4b6', padding: '8px 0' }}>
      <div className="govuk-width-container">
        <div className="govuk-body-s" style={{ margin: 0, display: 'flex', gap: '24px', alignItems: 'center' }}>
          <span>Expires {formatExpiry(session.expiresAt)}</span>
          <a href="/upload" className="govuk-link govuk-link--no-visited-state">
            View files ({session.fileCount})
          </a>
          {session.finalised && (
            <a href="/issues" className="govuk-link govuk-link--no-visited-state">
              View issues ({session.issueCount ?? 0})
            </a>
          )}
          <button type="button" onClick={handleStartAgain} className="govuk-link govuk-link--no-visited-state" style={linkStyle}>
            Start again
          </button>
        </div>
      </div>
    </div>
  )
}
