import { useState, useEffect, useCallback } from 'react'
import { displayFilename } from '@/lib/filename'
import Head from 'next/head'
import Link from 'next/link'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import Layout from '@/components/Layout'

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || 'http://localhost:8000'

interface RelevantPassage {
  document: string
  passage: string
}

interface Issue {
  source: string
  topic_label: string
  type: 'contradiction' | 'uneven_coverage' | 'compliance_violation'
  guideline_cited?: string
  description: string
  documents_involved: string[]
  relevant_passages: RelevantPassage[]
}

interface ModuleSummary {
  status: string
  total_topics: number
  checked_topics: number
  in_progress_topics: number
  issue_count: number
}

interface IssuesResponse {
  issues: Issue[]
  modules: Record<string, ModuleSummary>
  url_map: Record<string, string>
}

function getAuthHeaders(): Record<string, string> {
  const sessionId = sessionStorage.getItem('cross-check-session-id')
  const authToken = sessionStorage.getItem('prototype-auth-token')
  return {
    'Content-Type': 'application/json',
    ...(sessionId ? { 'X-Session-ID': sessionId } : {}),
    ...(authToken ? { 'X-Prototype-Auth': authToken } : {}),
  }
}

function IssueRow({ issue, urlMap, grouped }: { issue: Issue; urlMap: Record<string, string>; grouped?: boolean }) {
  const [expanded, setExpanded] = useState(false)
  const typeLabel =
    issue.type === 'contradiction'
      ? 'Contradiction'
      : issue.type === 'uneven_coverage'
        ? 'Uneven coverage'
        : 'Compliance violation'
  const tagColour = 'govuk-tag--yellow'
  const itemLabel = issue.source === 'compliance' ? 'Page' : 'Topic'

  return (
    <div
      style={grouped ? { paddingTop: '12px' } : {
        borderBottom: '1px solid #b1b4b6',
        paddingTop: '16px',
        paddingBottom: '16px',
      }}
    >
      <p className="govuk-body" style={{ marginBottom: '4px', display: 'flex', alignItems: 'baseline', gap: '8px', flexWrap: 'wrap' }}>
        <strong className={`govuk-tag ${tagColour}`} style={{ whiteSpace: 'nowrap', display: 'inline-block', flexShrink: 0, maxWidth: 'none' }}>
          {typeLabel}
        </strong>
        <span>{issue.description}</span>
      </p>
      {!grouped && (
        <p className="govuk-body-s" style={{ color: '#505a5f', marginBottom: '4px' }}>
          {itemLabel}: {issue.topic_label}
        </p>
      )}
      {issue.guideline_cited && (
        <div
          style={{
            borderLeft: '4px solid #d4351c',
            paddingLeft: '12px',
            marginBottom: '4px',
          }}
        >
          <p className="govuk-body-s" style={{ margin: 0, color: '#505a5f', fontStyle: 'italic' }}>
            &ldquo;{issue.guideline_cited}&rdquo;
          </p>
        </div>
      )}
      {!grouped && (
        <p className="govuk-body-s" style={{ color: '#505a5f', marginBottom: '4px' }}>
          Documents: {issue.documents_involved.map(displayFilename).join(', ')}
        </p>
      )}
      {issue.relevant_passages.length > 0 && (
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
          {issue.relevant_passages.map((p, i) => (
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
                {urlMap[p.document] && (
                  <a href={urlMap[p.document]} target="_blank" rel="noopener noreferrer" className="govuk-link govuk-link--no-visited-state">
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

function SummariseSection({ module, issues }: { module: string; issues: Issue[] }) {
  const [loading, setLoading] = useState(false)
  const [summary, setSummary] = useState<string | null>(null)
  const [summaryError, setSummaryError] = useState<string | null>(null)

  const handleSummarise = useCallback(async () => {
    setLoading(true)
    setSummaryError(null)
    try {
      const res = await fetch(`${API_BASE}/api/issues/${module}/summarise`, {
        method: 'POST',
        headers: getAuthHeaders(),
      })
      if (!res.ok) throw new Error()
      const json = await res.json()
      setSummary(json.summary)
    } catch {
      setSummaryError('Failed to generate summary.')
    } finally {
      setLoading(false)
    }
  }, [module])

  return (
    <div style={{ marginBottom: '16px' }}>
      {!summary && (
        <button
          type="button"
          className="govuk-button govuk-button--secondary"
          style={{ marginBottom: 0 }}
          onClick={handleSummarise}
          disabled={loading || issues.length === 0}
        >
          {loading ? 'Summarising…' : 'Summarise issues'}
        </button>
      )}
      {summaryError && (
        <p className="govuk-body-s" style={{ color: '#d4351c', marginTop: '8px' }}>
          {summaryError}
        </p>
      )}
      {summary && (
        <div className="govuk-inset-text" style={{ marginTop: '8px' }}>
          <div className="govuk-body-s passage-markdown">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{summary}</ReactMarkdown>
          </div>
          <button
            type="button"
            className="govuk-link govuk-link--no-visited-state govuk-body-s"
            style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}
            onClick={handleSummarise}
            disabled={loading}
          >
            {loading ? 'Regenerating…' : 'Regenerate summary'}
          </button>
        </div>
      )}
    </div>
  )
}

export default function Issues() {
  const [data, setData] = useState<IssuesResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const sessionId = sessionStorage.getItem('cross-check-session-id')
    if (!sessionId) {
      setError('No active session. Please upload files to get started.')
      return
    }

    fetch(`${API_BASE}/api/issues`, { headers: getAuthHeaders() })
      .then((r) => (r.ok ? r.json() : Promise.reject(r)))
      .then(setData)
      .catch(() => setError('Failed to load issues.'))
  }, [])

  const inconsistencies = data?.modules?.inconsistencies
  const compliance = data?.modules?.compliance
  const anyInProgress =
    (inconsistencies && (inconsistencies.status === 'discovering' || inconsistencies.in_progress_topics > 0)) ||
    (compliance && compliance.in_progress_topics > 0)

  return (
    <Layout showSessionBanner>
      <Head>
        <title>Issues found - Cross-check</title>
      </Head>

      <Link href="/analyse" className="govuk-back-link">
        Back
      </Link>

      <h1 className="govuk-heading-xl">Issues found</h1>

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

      {data && Object.keys(data.modules).length === 0 && (
        <p className="govuk-body">
          No analysis has been run yet.{' '}
          <Link href="/analyse" className="govuk-link">
            Go to analysis
          </Link>{' '}
          to get started.
        </p>
      )}

      {anyInProgress && (
        <div className="govuk-inset-text">
          <p className="govuk-body" style={{ margin: 0 }}>
            Analysis is still in progress — results shown so far may be incomplete.
          </p>
        </div>
      )}

      {inconsistencies && (
        <>
          <h2 className="govuk-heading-l">Inconsistencies</h2>
          <SummariseSection
            module="inconsistencies"
            issues={data?.issues.filter((i) => i.source === 'inconsistencies') ?? []}
          />
          <p className="govuk-body-s" style={{ color: '#505a5f' }}>
            {inconsistencies.checked_topics} of {inconsistencies.total_topics} topic
            {inconsistencies.total_topics !== 1 ? 's' : ''} checked
            {inconsistencies.in_progress_topics > 0
              ? ` · ${inconsistencies.in_progress_topics} in progress`
              : ''}
            {' · '}
            {inconsistencies.issue_count === 0
              ? 'No issues found so far'
              : `${inconsistencies.issue_count} issue${inconsistencies.issue_count !== 1 ? 's' : ''} found`}
          </p>

          {data.issues.filter((i) => i.source === 'inconsistencies').length === 0 ? (
            <p className="govuk-body">No inconsistencies found in checked topics.</p>
          ) : (
            <div>
              {data.issues
                .filter((i) => i.source === 'inconsistencies')
                .map((issue, i) => (
                  <IssueRow key={i} issue={issue} urlMap={data.url_map ?? {}} />
                ))}
            </div>
          )}
        </>
      )}

      {compliance && (
        <>
          <h2 className="govuk-heading-l">Compliance</h2>
          <SummariseSection
            module="compliance"
            issues={data?.issues.filter((i) => i.source === 'compliance') ?? []}
          />
          <p className="govuk-body-s" style={{ color: '#505a5f' }}>
            {compliance.checked_topics} of {compliance.total_topics} page
            {compliance.total_topics !== 1 ? 's' : ''} checked
            {compliance.in_progress_topics > 0
              ? ` · ${compliance.in_progress_topics} in progress`
              : ''}
            {' · '}
            {compliance.issue_count === 0
              ? 'No issues found so far'
              : `${compliance.issue_count} issue${compliance.issue_count !== 1 ? 's' : ''} found`}
          </p>

          {(() => {
            const urlMap = data.url_map ?? {}
            const complianceIssues = data.issues.filter((i) => i.source === 'compliance')
            if (complianceIssues.length === 0) {
              return <p className="govuk-body">No compliance issues found in checked pages.</p>
            }
            const byPage: Record<string, Issue[]> = {}
            for (const issue of complianceIssues) {
              ;(byPage[issue.topic_label] ??= []).push(issue)
            }
            return Object.entries(byPage).map(([page, issues]) => (
              <div key={page} style={{ borderTop: '2px solid #b1b4b6', paddingTop: '16px', marginBottom: '24px' }}>
                <div style={{ display: 'flex', gap: '12px', alignItems: 'baseline', marginBottom: '4px' }}>
                  <h3 className="govuk-heading-s" style={{ margin: 0 }}>{displayFilename(page)}</h3>
                  {/* eslint-disable-next-line security/detect-object-injection */}
                  {urlMap[page] && (
                    // eslint-disable-next-line security/detect-object-injection
                    <a href={urlMap[page]} target="_blank" rel="noopener noreferrer" className="govuk-link govuk-link--no-visited-state govuk-body-s">
                      View live version
                    </a>
                  )}
                </div>
                {issues.map((issue, i) => (
                  <IssueRow key={i} issue={issue} urlMap={urlMap} grouped />
                ))}
              </div>
            ))
          })()}
        </>
      )}

    </Layout>
  )
}
