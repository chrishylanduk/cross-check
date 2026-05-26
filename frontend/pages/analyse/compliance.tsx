import { useState, useEffect, useCallback, useRef } from 'react'
import { displayFilename } from '@/lib/filename'
import { useAuthHeaders } from '@/contexts/AuthContext'
import Head from 'next/head'
import Link from 'next/link'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import Layout from '@/components/Layout'
import { usePolling } from '@/hooks/usePolling'

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || 'http://localhost:8000'
const POLL_INTERVAL_MS = 2000
const MAX_GUIDELINES_LENGTH = 50_000

interface GuidelinePreset {
  filename: string
  label: string
}

interface RelevantPassage {
  document: string
  passage: string
}

interface ComplianceIssue {
  guideline_cited: string
  description: string
  relevant_passages: RelevantPassage[]
}

interface PageResult {
  has_issues: boolean
  issues: ComplianceIssue[]
}

interface Page {
  id: number
  filename: string
  url?: string | null
  check_status: null | 'checking' | 'complete' | 'error'
  result: PageResult | null
  error?: string
}

interface JobState {
  status: 'pages_ready' | 'error'
  pages: Page[]
  error: string | null
}

function useHeaders() {
  const getAuthHeaders = useAuthHeaders()
  return useCallback(async () => {
    const sessionId = sessionStorage.getItem('cross-check-session-id')
    const auth = await getAuthHeaders()
    return {
      'Content-Type': 'application/json',
      ...(sessionId ? { 'X-Session-ID': sessionId } : {}),
      ...auth,
    }
  }, [getAuthHeaders])
}

function GuidelinePresetDropdown({
  presets,
  onSelect,
}: {
  presets: GuidelinePreset[]
  onSelect: (content: string) => void
}) {
  const [loading, setLoading] = useState(false)
  const buildHeaders = useHeaders()

  const handleChange = async (e: React.ChangeEvent<HTMLSelectElement>) => {
    const filename = e.target.value
    if (!filename) return
    setLoading(true)
    try {
      const res = await fetch(`${API_BASE}/api/guidelines/${encodeURIComponent(filename)}`, {
        headers: await buildHeaders(),
      })
      if (res.ok) {
        onSelect(await res.text())
      }
    } finally {
      setLoading(false)
      e.target.value = ''
    }
  }

  if (presets.length === 0) return null

  return (
    <div className="govuk-form-group" style={{ marginBottom: '8px' }}>
      <label className="govuk-label govuk-label--s" htmlFor="guideline-preset">
        Load a preset
      </label>
      <select
        className="govuk-select"
        id="guideline-preset"
        defaultValue=""
        onChange={handleChange}
        disabled={loading}
        style={{ marginRight: '8px' }}
      >
        <option value="" disabled>
          {loading ? 'Loading…' : 'Choose a preset guidelines file…'}
        </option>
        {presets.map((p) => (
          <option key={p.filename} value={p.filename}>
            {p.label}
          </option>
        ))}
      </select>
    </div>
  )
}

function GuidelinesTextarea({
  id,
  label,
  value,
  onChange,
  rows = 12,
  placeholder,
}: {
  id: string
  label: string
  value: string
  onChange: (value: string) => void
  rows?: number
  placeholder?: string
}) {
  const remaining = MAX_GUIDELINES_LENGTH - value.length
  const isOver = remaining < 0
  const abs = Math.abs(remaining)
  const message = isOver
    ? `You have ${abs.toLocaleString()} character${abs !== 1 ? 's' : ''} too many`
    : `You have ${abs.toLocaleString()} character${abs !== 1 ? 's' : ''} remaining`

  return (
    <div className={`govuk-form-group${isOver ? ' govuk-form-group--error' : ''}`}>
      <label className="govuk-label" htmlFor={id}>
        {label}
      </label>
      <textarea
        className={`govuk-textarea${isOver ? ' govuk-textarea--error' : ''}`}
        id={id}
        rows={rows}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-describedby={`${id}-info`}
        placeholder={placeholder}
      />
      <div
        id={`${id}-info`}
        className={`govuk-character-count__message${isOver ? ' govuk-error-message' : ' govuk-hint'}`}
        aria-live="polite"
      >
        {message}
      </div>
    </div>
  )
}

function ComplianceIssueDetail({ item, pageUrl }: { item: ComplianceIssue; pageUrl?: string | null }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div style={{ marginBottom: '16px' }}>
      <p className="govuk-body" style={{ marginBottom: '4px', display: 'flex', alignItems: 'baseline', gap: '8px', flexWrap: 'wrap' }}>
        <strong className="govuk-tag govuk-tag--yellow" style={{ whiteSpace: 'nowrap', display: 'inline-block', flexShrink: 0, maxWidth: 'none' }}>
          Compliance violation
        </strong>
        <span>{item.description}</span>
      </p>
      <div
        style={{
          borderLeft: '4px solid #d4351c',
          paddingLeft: '12px',
          marginBottom: '4px',
        }}
      >
        <p className="govuk-body-s" style={{ margin: 0, color: '#505a5f', fontStyle: 'italic' }}>
          &ldquo;{item.guideline_cited}&rdquo;
        </p>
      </div>
      {item.relevant_passages.length > 0 && (
        <button
          type="button"
          className="govuk-link govuk-link--no-visited-state govuk-body-s"
          style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}
          onClick={() => setExpanded((e) => !e)}
        >
          {expanded ? 'Hide passages' : 'Show relevant passages'}
        </button>
      )}
      {expanded && (
        <div style={{ marginTop: '8px' }}>
          {item.relevant_passages.map((p, i) => (
            <div
              key={i}
              style={{
                borderLeft: '4px solid #b1b4b6',
                paddingLeft: '12px',
                marginBottom: '8px',
              }}
            >
              <p className="govuk-body-s" style={{ marginBottom: '2px', color: '#505a5f', display: 'flex', gap: '8px', alignItems: 'baseline' }}>
                <strong>{displayFilename(p.document)}</strong>
                {pageUrl && (
                  <a href={pageUrl} target="_blank" rel="noopener noreferrer" className="govuk-link govuk-link--no-visited-state">
                    View live version
                  </a>
                )}
              </p>
              <div className="govuk-body-s passage-markdown" style={{ margin: 0, fontStyle: 'italic' }}>
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{p.passage}</ReactMarkdown>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function PageRow({ page, onCheck }: { page: Page; onCheck: (pageId: number) => void }) {
  const [showResults, setShowResults] = useState(false)
  const [showContent, setShowContent] = useState(false)
  const [content, setContent] = useState<string | null>(null)
  const [contentLoading, setContentLoading] = useState(false)
  const hasResult = page.check_status === 'complete' && page.result
  const buildHeaders = useHeaders()

  const handleToggleContent = async () => {
    if (showContent) {
      setShowContent(false)
      return
    }
    setShowContent(true)
    if (content !== null) return
    setContentLoading(true)
    try {
      const res = await fetch(`${API_BASE}/api/collection/${encodeURIComponent(page.filename)}`, {
        headers: await buildHeaders(),
      })
      if (res.ok) {
        setContent(await res.text())
      } else {
        setContent('Unable to load page content.')
      }
    } catch {
      setContent('Unable to load page content.')
    } finally {
      setContentLoading(false)
    }
  }

  return (
    <>
      <tr className="govuk-table__row">
        <td className="govuk-table__cell" style={{ wordBreak: 'break-word' }}>
          <div>{displayFilename(page.filename)}</div>
          <div style={{ marginTop: '4px', display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
            <button
              type="button"
              className="govuk-link govuk-link--no-visited-state govuk-body-s"
              style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}
              onClick={handleToggleContent}
            >
              {showContent ? 'Hide content' : 'View content'}
            </button>
            {page.url && (
              <a
                href={page.url}
                target="_blank"
                rel="noopener noreferrer"
                className="govuk-link govuk-link--no-visited-state govuk-body-s"
              >
                View live version
              </a>
            )}
          </div>
        </td>
        <td className="govuk-table__cell">
          {page.check_status === null && (
            <button
              type="button"
              className="govuk-button govuk-button--secondary"
              style={{ marginBottom: 0 }}
              onClick={() => onCheck(page.id)}
            >
              Check
            </button>
          )}
          {page.check_status === 'checking' && (
            <span className="govuk-body-s" style={{ color: '#505a5f' }}>
              Checking&hellip;
            </span>
          )}
          {page.check_status === 'complete' && page.result && (
            <button
              type="button"
              className="govuk-link govuk-link--no-visited-state govuk-body-s"
              style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}
              onClick={() => setShowResults((s) => !s)}
            >
              {page.result.has_issues
                ? `${page.result.issues.length} issue${page.result.issues.length !== 1 ? 's' : ''} found`
                : 'No issues found'}{' '}
              {showResults ? '▲' : '▼'}
            </button>
          )}
          {page.check_status === 'error' && (
            <div>
              <span className="govuk-error-message govuk-body-s" style={{ display: 'block', marginBottom: '4px' }}>
                Check failed
              </span>
              <button
                type="button"
                className="govuk-button govuk-button--secondary"
                style={{ marginBottom: 0 }}
                onClick={() => onCheck(page.id)}
              >
                Retry
              </button>
            </div>
          )}
        </td>
      </tr>

      {showResults && hasResult && (
        <tr className="govuk-table__row">
          <td
            className="govuk-table__cell"
            colSpan={2}
            style={{ background: '#f3f2f1', paddingTop: '16px', paddingBottom: '16px', wordBreak: 'break-word' }}
          >
            {page.result!.issues.length === 0 ? (
              <p className="govuk-body-s" style={{ margin: 0 }}>
                No compliance issues found for this page.
              </p>
            ) : (
              page.result!.issues.map((item, i) => <ComplianceIssueDetail key={i} item={item} pageUrl={page.url} />)
            )}
          </td>
        </tr>
      )}

      {showContent && (
        <tr className="govuk-table__row">
          <td
            className="govuk-table__cell"
            colSpan={2}
            style={{ background: '#f8f8f8', paddingTop: '16px', paddingBottom: '16px', wordBreak: 'break-word' }}
          >
            {contentLoading ? (
              <p className="govuk-body-s" style={{ margin: 0, color: '#505a5f' }}>
                Loading&hellip;
              </p>
            ) : (
              <div className="govuk-body-s passage-markdown">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{content ?? ''}</ReactMarkdown>
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  )
}

export default function Compliance() {
  const [job, setJob] = useState<JobState | null>(null)
  const [guidelines, setGuidelines] = useState<string | null>(null)
  const [guidelinesInput, setGuidelinesInput] = useState('')
  const [showChangeGuidelines, setShowChangeGuidelines] = useState(false)
  const [changeGuidelinesInput, setChangeGuidelinesInput] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [presets, setPresets] = useState<GuidelinePreset[]>([])
  const buildHeaders = useHeaders()
  const startedRef = useRef(false)
  const { schedule, cancel } = usePolling(POLL_INTERVAL_MS)

  const pollJob = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/analysis/compliance`, {
        headers: await buildHeaders(),
      })
      if (!res.ok) return
      const data: JobState = await res.json()
      setJob(data)
      const anyChecking = data.pages.some((p) => p.check_status === 'checking')
      // eslint-disable-next-line react-hooks/immutability
      if (anyChecking) schedule(pollJob)
    } catch {
      // Silently ignore poll failures
    }
  }, [schedule, buildHeaders])

  useEffect(() => {
    if (startedRef.current) return
    startedRef.current = true

    if (!sessionStorage.getItem('cross-check-session-id')) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setError('No active session. Please upload files to get started.')
      return
    }

    const init = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/guidelines`, { headers: await buildHeaders() })
        if (res.ok) {
          const data = await res.json()
          setPresets(data.guidelines ?? [])
        }
      } catch {
        // Presets unavailable — proceed without them
      }

      try {
        const res = await fetch(`${API_BASE}/api/compliance/guidelines`, {
          headers: await buildHeaders(),
        })
        if (res.ok) {
          const data = await res.json()
          setGuidelines(data.guidelines)
          setChangeGuidelinesInput(data.guidelines)
        }
      } catch {
        // No guidelines set yet
      }

      try {
        const res = await fetch(`${API_BASE}/api/analysis/compliance`, {
          headers: await buildHeaders(),
        })
        if (res.ok) {
          const data: JobState = await res.json()
          setJob(data)
          const anyChecking = data.pages.some((p) => p.check_status === 'checking')
          if (anyChecking) schedule(pollJob)
        }
      } catch {
        // No job yet
      }
    }

    init()
    return () => cancel()
  }, [pollJob, schedule, cancel, buildHeaders])

  const saveGuidelinesAndStartJob = async (text: string): Promise<boolean> => {
    const res = await fetch(`${API_BASE}/api/compliance/guidelines`, {
      method: 'POST',
      headers: await buildHeaders(),
      body: JSON.stringify({ guidelines: text }),
    })
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      setError(typeof data.detail === 'string' ? data.detail : 'Failed to save guidelines.')
      return false
    }

    await fetch(`${API_BASE}/api/analysis/compliance`, {
      method: 'POST',
      headers: await buildHeaders(),
    })

    const jobRes = await fetch(`${API_BASE}/api/analysis/compliance`, {
      headers: await buildHeaders(),
    })
    if (jobRes.ok) {
      const data: JobState = await jobRes.json()
      setJob(data)
    }
    return true
  }

  const handleSaveGuidelines = async () => {
    const trimmed = guidelinesInput.trim()
    if (!trimmed) return
    setSaving(true)
    setError(null)
    try {
      const ok = await saveGuidelinesAndStartJob(trimmed)
      if (ok) {
        setGuidelines(trimmed)
        setChangeGuidelinesInput(trimmed)
      }
    } catch {
      setError('Unable to connect to the service. Please try again.')
    } finally {
      setSaving(false)
    }
  }

  const handleChangeGuidelines = async () => {
    const trimmed = changeGuidelinesInput.trim()
    if (!trimmed) return
    setSaving(true)
    setError(null)
    try {
      const ok = await saveGuidelinesAndStartJob(trimmed)
      if (ok) {
        setGuidelines(trimmed)
        setShowChangeGuidelines(false)
      }
    } catch {
      setError('Unable to connect to the service. Please try again.')
    } finally {
      setSaving(false)
    }
  }

  const handleCheckBatch = async () => {
    if (!job) return
    const unchecked = job.pages
      .filter((p) => p.check_status === null || p.check_status === 'error')
      .slice(0, 25)
    if (unchecked.length === 0) return
    const ids = unchecked.map((p) => p.id)
    setJob((prev) =>
      prev
        ? { ...prev, pages: prev.pages.map((p) => (ids.includes(p.id) ? { ...p, check_status: 'checking' } : p)) }
        : prev,
    )
    try {
      await fetch(`${API_BASE}/api/analysis/compliance/pages/check-batch`, {
        method: 'POST',
        headers: await buildHeaders(),
        body: JSON.stringify({ page_ids: ids }),
      })
    } catch {
      // Polling will surface any errors
    }
    schedule(pollJob)
  }

  const handleCheckAll = async () => {
    if (!job) return
    setJob((prev) =>
      prev
        ? {
            ...prev,
            pages: prev.pages.map((p) =>
              p.check_status === null || p.check_status === 'error' ? { ...p, check_status: 'checking' } : p,
            ),
          }
        : prev,
    )
    try {
      await fetch(`${API_BASE}/api/analysis/compliance/pages/check-all`, {
        method: 'POST',
        headers: await buildHeaders(),
      })
    } catch {
      // Polling will surface any errors
    }
    schedule(pollJob)
  }

  const handleCheck = async (pageId: number) => {
    setJob((prev) =>
      prev
        ? { ...prev, pages: prev.pages.map((p) => (p.id === pageId ? { ...p, check_status: 'checking' } : p)) }
        : prev,
    )
    try {
      await fetch(`${API_BASE}/api/analysis/compliance/pages/${pageId}/check`, {
        method: 'POST',
        headers: await buildHeaders(),
      })
    } catch {
      // Polling will surface any errors
    }
    schedule(pollJob)
  }

  const uncheckedCount =
    job?.pages.filter((p) => p.check_status === null || p.check_status === 'error').length ?? 0

  return (
    <Layout showSessionBanner>
      <Head>
        <title>Check for compliance - Cross-check</title>
      </Head>

      <Link href="/analyse" className="govuk-back-link">
        Back
      </Link>

      <h1 className="govuk-heading-xl">Check for compliance</h1>

      {error && (
        <div className="govuk-error-summary" data-module="govuk-error-summary">
          <div role="alert">
            <h2 className="govuk-error-summary__title">There is a problem</h2>
            <div className="govuk-error-summary__body">
              <p>{error}</p>
            </div>
          </div>
        </div>
      )}

      {/* Step 1: no guidelines set yet */}
      {!guidelines && !error && (
        <>
          <p className="govuk-body">
            Paste your content guidelines below. Each page in your collection will be checked against them.
          </p>
          <GuidelinePresetDropdown presets={presets} onSelect={setGuidelinesInput} />
          <GuidelinesTextarea
            id="guidelines-input"
            label="Content guidelines"
            value={guidelinesInput}
            onChange={setGuidelinesInput}
            placeholder="Paste your style guide, content standards, or house rules here…"
          />
          <button
            type="button"
            className="govuk-button"
            disabled={saving || !guidelinesInput.trim() || guidelinesInput.length > MAX_GUIDELINES_LENGTH}
            onClick={handleSaveGuidelines}
          >
            {saving ? 'Saving…' : 'Save guidelines and start checking'}
          </button>
        </>
      )}

      {/* Step 2: job ready */}
      {guidelines && job && (
        <>
          {!showChangeGuidelines ? (
            <p className="govuk-body-s" style={{ color: '#505a5f', marginBottom: '20px' }}>
              Checking against your content guidelines.{' '}
              <button
                type="button"
                className="govuk-link govuk-link--no-visited-state govuk-body-s"
                style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}
                onClick={() => setShowChangeGuidelines(true)}
              >
                Change guidelines
              </button>
            </p>
          ) : (
            <div className="govuk-inset-text" style={{ marginBottom: '20px' }}>
              <p className="govuk-body-s">
                <strong>Changing your guidelines will clear all compliance results for this session.</strong>
              </p>
              <GuidelinePresetDropdown presets={presets} onSelect={setChangeGuidelinesInput} />
              <GuidelinesTextarea
                id="change-guidelines-input"
                label="Updated content guidelines"
                value={changeGuidelinesInput}
                onChange={setChangeGuidelinesInput}
                rows={10}
              />
              <div className="govuk-button-group">
                <button
                  type="button"
                  className="govuk-button"
                  disabled={saving || !changeGuidelinesInput.trim() || changeGuidelinesInput.length > MAX_GUIDELINES_LENGTH}
                  onClick={handleChangeGuidelines}
                >
                  {saving ? 'Saving…' : 'Save and restart checking'}
                </button>
                <button
                  type="button"
                  className="govuk-link"
                  style={{ background: 'none', border: 'none', cursor: 'pointer' }}
                  onClick={() => {
                    setShowChangeGuidelines(false)
                    setChangeGuidelinesInput(guidelines)
                  }}
                >
                  Cancel
                </button>
              </div>
            </div>
          )}

          <p className="govuk-body">
            {job.pages.length === 0
              ? 'No pages found in your collection.'
              : `${job.pages.length} page${job.pages.length !== 1 ? 's' : ''} ready to check.`}
          </p>

          {uncheckedCount > 0 && (
            <div className="govuk-button-group" style={{ marginBottom: '20px' }}>
              <button type="button" className="govuk-button" onClick={handleCheckAll}>
                Check all {uncheckedCount} unchecked page{uncheckedCount !== 1 ? 's' : ''}
              </button>
              {uncheckedCount > 25 && (
                <button type="button" className="govuk-button govuk-button--secondary" onClick={handleCheckBatch}>
                  Check next 25
                </button>
              )}
            </div>
          )}

          {job.pages.length > 0 && (
            <table className="govuk-table" style={{ tableLayout: 'fixed', width: '100%' }}>
              <colgroup>
                <col />
                <col style={{ width: '14em' }} />
              </colgroup>
              <thead className="govuk-table__head">
                <tr className="govuk-table__row">
                  <th scope="col" className="govuk-table__header">
                    Page
                  </th>
                  <th scope="col" className="govuk-table__header">
                    Action
                  </th>
                </tr>
              </thead>
              <tbody className="govuk-table__body">
                {job.pages.map((page) => (
                  <PageRow key={page.id} page={page} onCheck={handleCheck} />
                ))}
              </tbody>
            </table>
          )}
        </>
      )}

      {/* Guidelines saved but no job yet (e.g. after page refresh before starting) */}
      {guidelines && !job && !error && (
        <>
          <p className="govuk-body">Your guidelines are saved. Start checking your pages against them.</p>
          <button
            type="button"
            className="govuk-button"
            disabled={saving}
            onClick={async () => {
              setSaving(true)
              setError(null)
              try {
                await fetch(`${API_BASE}/api/analysis/compliance`, {
                  method: 'POST',
                  headers: await buildHeaders(),
                })
                const res = await fetch(`${API_BASE}/api/analysis/compliance`, {
                  headers: await buildHeaders(),
                })
                if (res.ok) setJob(await res.json())
              } catch {
                setError('Unable to start analysis. Please try again.')
              } finally {
                setSaving(false)
              }
            }}
          >
            {saving ? 'Starting…' : 'Start compliance check'}
          </button>
        </>
      )}
    </Layout>
  )
}
