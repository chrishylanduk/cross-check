import { useState, useEffect, useCallback, useRef } from 'react'
import Head from 'next/head'
import Link from 'next/link'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import Layout from '@/components/Layout'

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || 'http://localhost:8000'
const POLL_INTERVAL_MS = 2000

interface RelevantPassage {
  document: string
  passage: string
}

interface Inconsistency {
  type: 'contradiction' | 'uneven_coverage'
  description: string
  documents_involved: string[]
  relevant_passages: RelevantPassage[]
}

interface TopicResult {
  has_inconsistencies: boolean
  inconsistencies: Inconsistency[]
}

interface TopicChunk {
  text: string
  source_file: string
}

interface Topic {
  id: number
  label: string
  chunk_count: number
  doc_count: number
  docs: string[]
  topic_chunks: TopicChunk[]
  check_status: null | 'checking' | 'complete' | 'error'
  result: TopicResult | null
  error?: string
}

interface JobState {
  status: 'discovering' | 'topics_ready' | 'error'
  topics: Topic[]
  error: string | null
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

function InconsistencyDetail({ item }: { item: Inconsistency }) {
  const [expanded, setExpanded] = useState(false)
  const typeLabel = item.type === 'contradiction' ? 'Contradiction' : 'Uneven coverage'

  return (
    <div style={{ marginBottom: '16px' }}>
      <p className="govuk-body" style={{ marginBottom: '4px' }}>
        <strong className="govuk-tag govuk-tag--yellow" style={{ marginRight: '8px' }}>
          {typeLabel}
        </strong>
        {item.description}
      </p>
      <p className="govuk-body-s" style={{ color: '#505a5f', marginBottom: '4px' }}>
        Documents involved: {item.documents_involved.join(', ')}
      </p>
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

function TopicRow({
  topic,
  onCheck,
}: {
  topic: Topic
  onCheck: (topicId: number) => void
}) {
  const [showResults, setShowResults] = useState(false)
  const [showChunks, setShowChunks] = useState(false)
  const hasResult = topic.check_status === 'complete' && topic.result

  return (
    <>
      <tr className="govuk-table__row">
        <td className="govuk-table__cell" style={{ wordBreak: 'break-word' }}>
          <span style={{ display: 'block' }}>{topic.label}</span>
          {topic.topic_chunks.length > 0 && (
            <button
              type="button"
              className="govuk-link govuk-link--no-visited-state govuk-body-s"
              style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}
              onClick={() => setShowChunks((s) => !s)}
            >
              {showChunks ? 'Hide passages' : 'View passages'}
            </button>
          )}
        </td>
        <td className="govuk-table__cell govuk-table__cell--numeric">{topic.doc_count}</td>
        <td className="govuk-table__cell govuk-table__cell--numeric">{topic.chunk_count}</td>
        <td className="govuk-table__cell">
          {topic.check_status === null && (
            <button
              type="button"
              className="govuk-button govuk-button--secondary"
              style={{ marginBottom: 0 }}
              onClick={() => onCheck(topic.id)}
            >
              Check
            </button>
          )}
          {topic.check_status === 'checking' && (
            <span className="govuk-body-s" style={{ color: '#505a5f' }}>
              Checking...
            </span>
          )}
          {topic.check_status === 'complete' && topic.result && (
            <button
              type="button"
              className="govuk-link govuk-link--no-visited-state govuk-body-s"
              style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}
              onClick={() => setShowResults((s) => !s)}
            >
              {topic.result.has_inconsistencies
                ? `${topic.result.inconsistencies.length} issue${topic.result.inconsistencies.length !== 1 ? 's' : ''} found`
                : 'No issues found'}{' '}
              {showResults ? '▲' : '▼'}
            </button>
          )}
          {topic.check_status === 'error' && (
            <span className="govuk-error-message govuk-body-s">Check failed</span>
          )}
        </td>
      </tr>

      {showChunks && (
        <tr className="govuk-table__row">
          <td
            className="govuk-table__cell"
            colSpan={4}
            style={{ background: '#f3f2f1', paddingTop: '16px', paddingBottom: '16px', wordBreak: 'break-word' }}
          >
            <h3 className="govuk-heading-s" style={{ marginBottom: '12px' }}>
              Passages in this topic ({topic.topic_chunks.length})
            </h3>
            {topic.topic_chunks.map((chunk, i) => (
              <div
                key={i}
                style={{
                  borderLeft: '4px solid #b1b4b6',
                  paddingLeft: '12px',
                  marginBottom: '16px',
                }}
              >
                <p className="govuk-body-s" style={{ marginBottom: '4px', color: '#505a5f' }}>
                  <strong>{chunk.source_file}</strong>
                </p>
                <div className="govuk-body-s passage-markdown" style={{ margin: 0 }}>
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{chunk.text}</ReactMarkdown>
                </div>
              </div>
            ))}
          </td>
        </tr>
      )}

      {showResults && hasResult && (
        <tr className="govuk-table__row">
          <td
            className="govuk-table__cell"
            colSpan={4}
            style={{ background: '#f3f2f1', paddingTop: '16px', paddingBottom: '16px', wordBreak: 'break-word' }}
          >
            {topic.result!.inconsistencies.length === 0 ? (
              <p className="govuk-body-s" style={{ margin: 0 }}>
                No inconsistencies found for this topic.
              </p>
            ) : (
              topic.result!.inconsistencies.map((item, i) => (
                <InconsistencyDetail key={i} item={item} />
              ))
            )}
          </td>
        </tr>
      )}
    </>
  )
}

export default function Inconsistencies() {
  const [jobId, setJobId] = useState<string | null>(null)
  const [job, setJob] = useState<JobState | null>(null)
  const [startError, setStartError] = useState<string | null>(null)
  const startedRef = useRef(false)
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const pollJob = useCallback(
    async (id: string) => {
      try {
        const res = await fetch(`${API_BASE}/api/analysis/${id}`, {
          headers: getAuthHeaders(),
        })
        if (!res.ok) {
          setJob({ status: 'error', topics: [], error: 'Failed to fetch analysis status.' })
          return
        }
        const data: JobState = await res.json()
        setJob(data)

        if (data.status === 'discovering') {
          pollRef.current = setTimeout(() => pollJob(id), POLL_INTERVAL_MS)
        } else {
          // Poll less frequently to pick up topic check results
          const anyChecking = data.topics.some((t) => t.check_status === 'checking')
          if (anyChecking) {
            pollRef.current = setTimeout(() => pollJob(id), POLL_INTERVAL_MS)
          }
        }
      } catch {
        setJob({ status: 'error', topics: [], error: 'Network error. Please try again.' })
      }
    },
    [],
  )

  // Start analysis on mount, resuming cached job if available
  useEffect(() => {
    if (startedRef.current) return
    startedRef.current = true

    const startAnalysis = async () => {
      // Resume existing job if one is cached for this session
      const cachedJobId = sessionStorage.getItem('cross-check-job-id')
      if (cachedJobId) {
        try {
          const res = await fetch(`${API_BASE}/api/analysis/${cachedJobId}`, {
            headers: getAuthHeaders(),
          })
          if (res.ok) {
            const data: JobState = await res.json()
            setJobId(cachedJobId)
            setJob(data)
            // Resume polling if still in progress
            const anyChecking = data.topics.some((t) => t.check_status === 'checking')
            if (data.status === 'discovering' || anyChecking) {
              pollRef.current = setTimeout(() => pollJob(cachedJobId), POLL_INTERVAL_MS)
            }
            return
          }
          // 404 or other error — server restarted, create a new job
          sessionStorage.removeItem('cross-check-job-id')
        } catch {
          sessionStorage.removeItem('cross-check-job-id')
        }
      }

      // No cached job (or it expired) — start a new one
      try {
        const res = await fetch(`${API_BASE}/api/analysis/inconsistencies`, {
          method: 'POST',
          headers: getAuthHeaders(),
        })
        if (!res.ok) {
          const data = await res.json().catch(() => ({}))
          setStartError(data.detail || 'Failed to start analysis.')
          return
        }
        const data = await res.json()
        sessionStorage.setItem('cross-check-job-id', data.job_id)
        setJobId(data.job_id)
        pollJob(data.job_id)
      } catch {
        setStartError('Unable to connect to the service. Please try again.')
      }
    }

    startAnalysis()

    return () => {
      if (pollRef.current) clearTimeout(pollRef.current)
    }
  }, [pollJob])

  // Resume polling when a topic check is triggered
  const handleCheck = async (topicId: number) => {
    if (!jobId) return

    // Optimistically mark as checking
    setJob((prev) =>
      prev
        ? {
            ...prev,
            topics: prev.topics.map((t) =>
              t.id === topicId ? { ...t, check_status: 'checking' } : t,
            ),
          }
        : prev,
    )

    try {
      await fetch(`${API_BASE}/api/analysis/${jobId}/topics/${topicId}/check`, {
        method: 'POST',
        headers: getAuthHeaders(),
      })
    } catch {
      // Polling will surface any errors
    }

    // Start polling to pick up the result
    if (pollRef.current) clearTimeout(pollRef.current)
    pollRef.current = setTimeout(() => pollJob(jobId), POLL_INTERVAL_MS)
  }

  return (
    <Layout showSessionBanner>
      <Head>
        <title>Check for inconsistencies - Cross-check</title>
      </Head>

      <Link href="/analyse" className="govuk-back-link">
        Back
      </Link>

      <div className="govuk-grid-row">
      <div className="govuk-grid-column-full">

      <h1 className="govuk-heading-xl">Check for inconsistencies</h1>

      {startError && (
        <div className="govuk-error-summary" data-module="govuk-error-summary">
          <div role="alert">
            <h2 className="govuk-error-summary__title">There is a problem</h2>
            <div className="govuk-error-summary__body">
              <p>{startError}</p>
            </div>
          </div>
        </div>
      )}

      {!startError && !job && (
        <p className="govuk-body">Starting analysis&hellip;</p>
      )}

      {job?.status === 'discovering' && (
        <p className="govuk-body">Discovering topics in your documents&hellip;</p>
      )}

      {job?.status === 'error' && (
        <div className="govuk-error-summary" data-module="govuk-error-summary">
          <div role="alert">
            <h2 className="govuk-error-summary__title">Analysis failed</h2>
            <div className="govuk-error-summary__body">
              <p>{job.error || 'An unexpected error occurred.'}</p>
            </div>
          </div>
        </div>
      )}

      {job?.status === 'topics_ready' && (
        <>
          <p className="govuk-body">
            {job.topics.length === 0
              ? 'No topics could be identified in your documents.'
              : `${job.topics.length} topic${job.topics.length !== 1 ? 's' : ''} found in your documents. Select a topic to check for inconsistencies.`}
          </p>

          {job.topics.length > 0 && (
            <table className="govuk-table" style={{ tableLayout: 'fixed', width: '100%' }}>
              <colgroup>
                <col />
                <col style={{ width: '8em' }} />
                <col style={{ width: '7em' }} />
                <col style={{ width: '12em' }} />
              </colgroup>
              <thead className="govuk-table__head">
                <tr className="govuk-table__row">
                  <th scope="col" className="govuk-table__header">
                    Topic
                  </th>
                  <th
                    scope="col"
                    className="govuk-table__header govuk-table__header--numeric"
                  >
                    Documents
                  </th>
                  <th
                    scope="col"
                    className="govuk-table__header govuk-table__header--numeric"
                  >
                    Passages
                  </th>
                  <th scope="col" className="govuk-table__header">
                    Action
                  </th>
                </tr>
              </thead>
              <tbody className="govuk-table__body">
                {job.topics.map((topic) => (
                  <TopicRow key={topic.id} topic={topic} onCheck={handleCheck} />
                ))}
              </tbody>
            </table>
          )}
        </>
      )}

      </div>
      </div>
    </Layout>
  )
}
