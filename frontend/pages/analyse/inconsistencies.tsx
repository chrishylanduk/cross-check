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
  phase?: 'chunking' | 'embedding' | 'modelling' | null
  topics: Topic[]
  error: string | null
  url_map?: Record<string, string>
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

function InconsistencyDetail({ item, urlMap }: { item: Inconsistency; urlMap: Record<string, string> }) {
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
        Documents involved: {item.documents_involved.map(displayFilename).join(', ')}
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

function TopicRow({
  topic,
  onCheck,
  urlMap,
}: {
  topic: Topic
  onCheck: (topicId: number) => void
  urlMap: Record<string, string>
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
            <div>
              <span className="govuk-error-message govuk-body-s" style={{ display: 'block', marginBottom: '4px' }}>
                Check failed
              </span>
              <button
                type="button"
                className="govuk-button govuk-button--secondary"
                style={{ marginBottom: 0 }}
                onClick={() => onCheck(topic.id)}
              >
                Retry
              </button>
            </div>
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
                <p className="govuk-body-s" style={{ marginBottom: '4px', color: '#505a5f', display: 'flex', gap: '8px', alignItems: 'baseline' }}>
                  <strong>{displayFilename(chunk.source_file)}</strong>
                  {urlMap[chunk.source_file] && (
                    <a href={urlMap[chunk.source_file]} target="_blank" rel="noopener noreferrer" className="govuk-link govuk-link--no-visited-state">
                      View live version
                    </a>
                  )}
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
                <InconsistencyDetail key={i} item={item} urlMap={urlMap} />
              ))
            )}
          </td>
        </tr>
      )}
    </>
  )
}

export default function Inconsistencies() {
  const [job, setJob] = useState<JobState | null>(null)
  const [startError, setStartError] = useState<string | null>(null)
  const startedRef = useRef(false)
  const { schedule, cancel } = usePolling(POLL_INTERVAL_MS)
  const buildHeaders = useHeaders()

  const pollJob = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/analysis/inconsistencies`, {
        headers: await buildHeaders(),
      })
      if (!res.ok) {
        setJob({ status: 'error', topics: [], error: 'Failed to fetch analysis status.' })
        return
      }
      const data: JobState = await res.json()
      setJob(data)

      if (data.status === 'discovering') {
        // eslint-disable-next-line react-hooks/immutability
        schedule(pollJob)
      } else {
        const anyChecking = data.topics.some((t) => t.check_status === 'checking')
        if (anyChecking) {
          schedule(pollJob)
        }
      }
    } catch {
      setJob({ status: 'error', topics: [], error: 'Network error. Please try again.' })
    }
  }, [schedule, buildHeaders])

  // On mount: try to resume an existing job, otherwise start a new one
  useEffect(() => {
    if (startedRef.current) return
    startedRef.current = true

    const startAnalysis = async () => {
      if (!sessionStorage.getItem('cross-check-session-id')) {
        setStartError('No active session. Please upload files to get started.')
        return
      }

      // Check if a job already exists for this session
      try {
        const res = await fetch(`${API_BASE}/api/analysis/inconsistencies`, {
          headers: await buildHeaders(),
        })
        if (res.ok) {
          const data: JobState = await res.json()
          setJob(data)
          const anyChecking = data.topics.some((t) => t.check_status === 'checking')
          if (data.status === 'discovering' || anyChecking) {
            schedule(pollJob)
          }
          return
        }
      } catch {
        // Fall through to start a new job
      }

      // No existing job — start one
      try {
        const res = await fetch(`${API_BASE}/api/analysis/inconsistencies`, {
          method: 'POST',
          headers: await buildHeaders(),
        })
        if (!res.ok) {
          const data = await res.json().catch(() => ({}))
          const detail = data.detail
          setStartError(typeof detail === 'string' ? detail : 'Failed to start analysis.')
          return
        }
        pollJob()
      } catch {
        setStartError('Unable to connect to the service. Please try again.')
      }
    }

    startAnalysis()

    return () => cancel()
  }, [pollJob, schedule, cancel, buildHeaders])

  const handleCheckBatch = async () => {
    if (!job) return

    const unchecked = job.topics.filter((t) => t.check_status === null || t.check_status === 'error').slice(0, 25)
    if (unchecked.length === 0) return

    const ids = unchecked.map((t) => t.id)

    setJob((prev) =>
      prev
        ? {
            ...prev,
            topics: prev.topics.map((t) =>
              ids.includes(t.id) ? { ...t, check_status: 'checking' } : t,
            ),
          }
        : prev,
    )

    try {
      await fetch(`${API_BASE}/api/analysis/inconsistencies/topics/check-batch`, {
        method: 'POST',
        headers: await buildHeaders(),
        body: JSON.stringify({ topic_ids: ids }),
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
            topics: prev.topics.map((t) =>
              t.check_status === null || t.check_status === 'error'
                ? { ...t, check_status: 'checking' }
                : t,
            ),
          }
        : prev,
    )

    try {
      await fetch(`${API_BASE}/api/analysis/inconsistencies/topics/check-all`, {
        method: 'POST',
        headers: await buildHeaders(),
      })
    } catch {
      // Polling will surface any errors
    }

    schedule(pollJob)
  }

  const handleCheck = async (topicId: number) => {
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
      await fetch(`${API_BASE}/api/analysis/inconsistencies/topics/${topicId}/check`, {
        method: 'POST',
        headers: await buildHeaders(),
      })
    } catch {
      // Polling will surface any errors
    }

    schedule(pollJob)
  }

  return (
    <Layout showSessionBanner>
      <Head>
        <title>Check for inconsistencies - Cross-check</title>
      </Head>

      <Link href="/analyse" className="govuk-back-link">
        Back
      </Link>

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
        <p className="govuk-body">
          {job.phase === 'chunking' && 'Reading your documents…'}
          {job.phase === 'embedding' && 'Analysing content…'}
          {job.phase === 'modelling' && 'Grouping content into topics…'}
          {!job.phase && 'Starting analysis…'}
        </p>
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

          {job.topics.length > 0 && (() => {
            const uncheckedCount = job.topics.filter((t) => t.check_status === null || t.check_status === 'error').length
            const batchSize = Math.min(25, uncheckedCount)
            if (uncheckedCount === 0) return null
            return (
              <div className="govuk-button-group" style={{ marginBottom: '20px' }}>
                <button
                  type="button"
                  className="govuk-button"
                  onClick={handleCheckAll}
                >
                  Check all {uncheckedCount} unchecked topic{uncheckedCount !== 1 ? 's' : ''}
                </button>
                {uncheckedCount > 25 && (
                  <button
                    type="button"
                    className="govuk-button govuk-button--secondary"
                    onClick={handleCheckBatch}
                  >
                    Check next {batchSize}
                  </button>
                )}
              </div>
            )
          })()}

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
                  <TopicRow key={topic.id} topic={topic} onCheck={handleCheck} urlMap={job.url_map ?? {}} />
                ))}
              </tbody>
            </table>
          )}
        </>
      )}

    </Layout>
  )
}
