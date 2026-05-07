import { useState, useEffect } from 'react'
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
  type: 'contradiction' | 'uneven_coverage'
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

function IssueRow({ issue }: { issue: Issue }) {
  const [expanded, setExpanded] = useState(false)
  const typeLabel = issue.type === 'contradiction' ? 'Contradiction' : 'Uneven coverage'

  return (
    <div
      style={{
        borderBottom: '1px solid #b1b4b6',
        paddingTop: '16px',
        paddingBottom: '16px',
      }}
    >
      <p className="govuk-body" style={{ marginBottom: '4px' }}>
        <strong className="govuk-tag govuk-tag--yellow" style={{ marginRight: '8px' }}>
          {typeLabel}
        </strong>
        {issue.description}
      </p>
      <p className="govuk-body-s" style={{ color: '#505a5f', marginBottom: '4px' }}>
        Topic: {issue.topic_label}
      </p>
      <p className="govuk-body-s" style={{ color: '#505a5f', marginBottom: '4px' }}>
        Documents: {issue.documents_involved.join(', ')}
      </p>
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
              <p className="govuk-body-s" style={{ marginBottom: '2px', color: '#505a5f' }}>
                <strong>{p.document}</strong>
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
  const anyInProgress =
    inconsistencies &&
    (inconsistencies.status === 'discovering' || inconsistencies.in_progress_topics > 0)

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
                  <IssueRow key={i} issue={issue} />
                ))}
            </div>
          )}
        </>
      )}

    </Layout>
  )
}
