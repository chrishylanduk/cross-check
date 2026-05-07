import { useState, useEffect, useCallback, useRef } from 'react'
import { useRouter } from 'next/router'
import { usePolling } from '@/hooks/usePolling'

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || 'http://localhost:8000'
const POLL_INTERVAL_MS = 5000

interface SessionData {
  fileCount: number
  issueCount: number | null
  expiresAt: number
  finalised: boolean
}

function sumIssues(modules: Record<string, { issue_count: number }>): number {
  return Object.values(modules).reduce((sum, m) => sum + m.issue_count, 0)
}

export default function SessionBanner() {
  const router = useRouter()
  const [session, setSession] = useState<SessionData | null>(null)
  const [finalised, setFinalised] = useState(false)
  const { schedule, cancel } = usePolling(POLL_INTERVAL_MS)
  const startedRef = useRef(false)

  const pollIssues = useCallback(async (headers: Record<string, string>) => {
    try {
      const issues = await fetch(`${API_BASE}/api/issues`, { headers }).then((r) => (r.ok ? r.json() : null))
      if (issues) {
        setSession((prev) => prev ? { ...prev, issueCount: sumIssues(issues.modules) } : prev)
      }
      schedule(() => pollIssues(headers))
    } catch {
      // Silently ignore poll failures
    }
  }, [schedule])

  useEffect(() => {
    if (startedRef.current) return
    startedRef.current = true

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
      const isFinalised = collection.finalised
      const issueCount = issues ? sumIssues(issues.modules) : 0
      setFinalised(isFinalised)
      setSession({
        fileCount: collection.file_count,
        issueCount: isFinalised ? issueCount : null,
        expiresAt: Number(expiresAt),
        finalised: isFinalised,
      })
      if (isFinalised) {
        schedule(() => pollIssues(headers))
      }
    }).catch(() => {})

    return () => cancel()
  }, [schedule, cancel, pollIssues])

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
          {finalised && (
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
