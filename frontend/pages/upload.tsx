import { useState, useEffect, useRef, useCallback } from 'react'
import Head from 'next/head'
import { useRouter } from 'next/router'
import type { GetServerSideProps } from 'next'
import Layout from '@/components/Layout'
import DataUsageNotice from '@/components/DataUsageNotice'
import FileList from '@/components/FileList'
import MarkdownViewModal from '@/components/MarkdownViewModal'
import { useAuthHeaders } from '@/contexts/AuthContext'

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || 'http://localhost:8000'
const MAX_FILE_SIZE = 50 * 1024 * 1024 // 50MB
const MAX_FILES = 5000

interface UploadProps {
  aiProviderName: string
  aiPrivacyPolicyUrl: string
}

interface FileInfo {
  name: string
  size: number
}

interface RejectedFile {
  name: string
  reason: string
}

interface StorageInfo {
  files: FileInfo[]
  finalised: boolean
  file_count: number
  storage_used: number
  storage_limit: number
}

export const getServerSideProps: GetServerSideProps<UploadProps> = async () => {
  return {
    props: {
      aiProviderName: process.env.AI_PROVIDER_NAME || 'OpenAI',
      aiPrivacyPolicyUrl: process.env.AI_PRIVACY_POLICY_URL || 'https://openai.com/en-GB/policies/eu-privacy-policy/',
    },
  }
}

export default function Upload({ aiProviderName, aiPrivacyPolicyUrl }: UploadProps) {
  const router = useRouter()
  const getAuthHeaders = useAuthHeaders()
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [expiresAt, setExpiresAt] = useState<number | null>(null)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [rejectedFiles, setRejectedFiles] = useState<RejectedFile[]>([])
  const [storageInfo, setStorageInfo] = useState<StorageInfo | null>(null)
  const [dataUsageAccepted, setDataUsageAccepted] = useState(false)
  const [modalFilename, setModalFilename] = useState<string | null>(null)
  const [modalContent, setModalContent] = useState<string | null>(null)
  const [modalLoading, setModalLoading] = useState(false)
  const [modalError, setModalError] = useState<string | null>(null)
  const [stripBeforeH1, setStripBeforeH1] = useState(false)
  const [footerCutoff, setFooterCutoff] = useState('')
  const fileInputRef = useRef<HTMLInputElement>(null)
  const folderInputRef = useRef<HTMLInputElement>(null)
  const initialisingRef = useRef<boolean>(false)

  // Check data usage acceptance
  useEffect(() => {
    const accepted = sessionStorage.getItem('data-usage-accepted')
    if (accepted === 'true') {
      setDataUsageAccepted(true)
    }
  }, [])

  // Re-initialise GOV.UK Frontend when form becomes visible
  useEffect(() => {
    if (dataUsageAccepted) {
      import('govuk-frontend/dist/govuk/govuk-frontend.min.js').then((GOVUKFrontend) => {
        GOVUKFrontend.initAll()
      })
    }
  }, [dataUsageAccepted])

  // Re-initialise file upload component when upload controls are rendered
  useEffect(() => {
    if (dataUsageAccepted && storageInfo && !storageInfo.finalised) {
      // Small delay to ensure DOM is updated
      setTimeout(() => {
        import('govuk-frontend/dist/govuk/govuk-frontend.min.js').then((GOVUKFrontend) => {
          GOVUKFrontend.initAll()
        })
      }, 100)
    }
  }, [dataUsageAccepted, storageInfo])

  const fetchStorageInfo = useCallback(async (sid: string) => {
    try {
      const authHeaders = await getAuthHeaders()
      const response = await fetch(`${API_BASE}/api/collection`, {
        headers: {
          'X-Session-ID': sid,
          ...authHeaders,
        },
      })
      if (response.status === 401) {
        sessionStorage.removeItem('cross-check-session-id')
        setSessionId(null)
        return
      }
      if (response.ok) {
        const data = await response.json()
        setStorageInfo(data)
      }
    } catch {
      // Silently fail - storage info is not critical
    }
  }, [getAuthHeaders])

  // Initialise session — also re-runs when sessionId is cleared (e.g. start again)
  useEffect(() => {
    if (typeof window === 'undefined') return
    if (!dataUsageAccepted) return
    if (sessionId !== null) return

    const initSession = async () => {
      // Prevent duplicate initialisation (React StrictMode runs effects twice)
      if (initialisingRef.current) return
      initialisingRef.current = true

      // Check for existing session
      const existingSession = sessionStorage.getItem('cross-check-session-id')
      if (existingSession) {
        const storedExpiry = sessionStorage.getItem('cross-check-expires-at')
        if (storedExpiry) setExpiresAt(Number(storedExpiry))
        setSessionId(existingSession)
        await fetchStorageInfo(existingSession)
        return
      }

      // Create new session
      try {
        const authHeaders = await getAuthHeaders()
        const response = await fetch(`${API_BASE}/api/session`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...authHeaders,
          },
        })

        if (!response.ok) {
          const errorText = await response.text()
          console.error('Session creation failed:', response.status, errorText)
          throw new Error(`Server returned ${response.status}`)
        }

        const data = await response.json()
        setSessionId(data.session_id)
        setExpiresAt(data.expires_at)
        sessionStorage.setItem('cross-check-session-id', data.session_id)
        sessionStorage.setItem('cross-check-expires-at', String(data.expires_at))
      } catch (err) {
        console.error('Session initialisation error:', err)
        setError('Unable to connect to the service. Please try again in a few moments.')
      }
    }

    initSession()
  }, [dataUsageAccepted, sessionId, fetchStorageInfo, getAuthHeaders])

  const handleDeleteFile = async (filename: string) => {
    try {
      const authHeaders = await getAuthHeaders()
      const response = await fetch(`${API_BASE}/api/collection/${encodeURIComponent(filename)}`, {
        method: 'DELETE',
        headers: {
          'X-Session-ID': sessionId as string,
          ...authHeaders,
        },
      })

      if (response.ok) {
        setSuccess(`Deleted ${filename}`)
        await fetchStorageInfo(sessionId as string)
        setTimeout(() => setSuccess(null), 3000)
      } else {
        const data = await response.json()
        setError(data.detail || 'Failed to delete file')
      }
    } catch {
      setError('Failed to delete file')
    }
  }

  const handleClearAll = async () => {
    const isFinalised = storageInfo?.finalised

    try {
      const authHeaders = await getAuthHeaders()

      if (isFinalised) {
        // Drop the old session so the init effect creates a fresh one
        sessionStorage.removeItem('cross-check-session-id')
        sessionStorage.removeItem('cross-check-expires-at')
        sessionStorage.removeItem('cross-check-finalised')
        sessionStorage.removeItem('cross-check-has-files')

        initialisingRef.current = false
        setSessionId(null)
        setExpiresAt(null)
        setStorageInfo(null)
        setSuccess('All files deleted')
        setTimeout(() => setSuccess(null), 3000)
      } else {
        // Just clear files
        const response = await fetch(`${API_BASE}/api/collection`, {
          method: 'DELETE',
          headers: {
            'X-Session-ID': sessionId as string,
            ...authHeaders,
          },
        })

        if (response.ok) {
          sessionStorage.removeItem('cross-check-session-id')
          sessionStorage.removeItem('cross-check-expires-at')
          sessionStorage.removeItem('cross-check-has-files')

          initialisingRef.current = false
          setSessionId(null)
          setExpiresAt(null)
          setStorageInfo(null)
          setSuccess('All files deleted')
          setTimeout(() => setSuccess(null), 3000)
        } else {
          const data = await response.json()
          setError(data.detail || 'Failed to clear files')
        }
      }
    } catch {
      setError('Failed to clear files')
    }
  }

  const handleFinalise = async () => {
    try {
      const authHeaders = await getAuthHeaders()
      const response = await fetch(`${API_BASE}/api/collection/finalise`, {
        method: 'POST',
        headers: {
          'X-Session-ID': sessionId as string,
          ...authHeaders,
        },
      })

      if (response.ok) {
        sessionStorage.setItem('cross-check-finalised', 'true')
        router.push('/analyse')
      } else {
        const data = await response.json()
        setError(data.detail || 'Failed to start analysis')
      }
    } catch {
      setError('Failed to start analysis')
    }
  }

  const handleViewFile = async (filename: string) => {
    setModalFilename(filename)
    setModalContent(null)
    setModalError(null)
    setModalLoading(true)

    try {
      const authHeaders = await getAuthHeaders()
      const response = await fetch(`${API_BASE}/api/collection/${encodeURIComponent(filename)}`, {
        headers: {
          'X-Session-ID': sessionId as string,
          ...authHeaders,
        },
      })

      if (!response.ok) {
        const data = await response.json().catch(() => ({}))
        throw new Error(data.detail || 'Could not load file content')
      }

      setModalContent(await response.text())
    } catch (err) {
      setModalError((err as Error).message || 'Could not load file content')
    } finally {
      setModalLoading(false)
    }
  }

  const handleCloseModal = () => {
    setModalFilename(null)
    setModalContent(null)
    setModalError(null)
  }

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFiles = Array.from(e.target.files ?? [])

    setError(null)
    setSuccess(null)
    setRejectedFiles([])

    if (selectedFiles.length === 0) return

    if (selectedFiles.length > MAX_FILES) {
      setError(`Maximum ${MAX_FILES} files allowed per upload`)
      return
    }

    // Check file sizes
    const oversizedFiles = selectedFiles.filter((f) => f.size > MAX_FILE_SIZE)
    if (oversizedFiles.length > 0) {
      setError(`Some files exceed the 50MB limit: ${oversizedFiles.map((f) => f.name).join(', ')}`)
      return
    }

    // Upload immediately
    setUploading(true)

    try {
      const formData = new FormData()
      selectedFiles.forEach((file) => {
        formData.append('files', file)
      })
      formData.append('strip_before_h1', stripBeforeH1 ? 'true' : 'false')
      formData.append('footer_cutoff', footerCutoff.trim())

      const authHeaders = await getAuthHeaders()
      const response = await fetch(`${API_BASE}/api/upload`, {
        method: 'POST',
        headers: {
          'X-Session-ID': sessionId as string,
          ...authHeaders,
        },
        body: formData,
      })

      if (response.status === 401) {
        sessionStorage.removeItem('cross-check-session-id')
        setSessionId(null)
        throw new Error('Session expired. Please try uploading again.')
      }

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(errorData.detail || `Upload failed with status ${response.status}`)
      }

      const result = await response.json()

      if (result.rejected_files?.length > 0) {
        setRejectedFiles(result.rejected_files)
      }

      if (result.file_count > 0) {
        sessionStorage.setItem('cross-check-has-files', 'true')
        setSuccess(
          `${result.file_count} file${result.file_count !== 1 ? 's' : ''} uploaded successfully`,
        )
      } else if (result.rejected_files?.length > 0) {
        setError('No files could be uploaded. Check the list below for details.')
      }

      // Clear file inputs
      if (fileInputRef.current) fileInputRef.current.value = ''
      if (folderInputRef.current) folderInputRef.current.value = ''

      // Update storage info
      await fetchStorageInfo(sessionId as string)
    } catch (err) {
      setError((err as Error).message || 'Failed to upload files')
    } finally {
      setUploading(false)
    }
  }

  const formatExpiry = (ts: number): string =>
    new Date(ts * 1000).toLocaleString('en-GB', {
      weekday: 'long',
      day: 'numeric',
      month: 'long',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })

  const formatBytes = (bytes: number): string => {
    if (bytes === 0) return '0 Bytes'
    const k = 1024
    const sizes = ['Bytes', 'KB', 'MB', 'GB']
    const i = Math.floor(Math.log(bytes) / Math.log(k))
    // eslint-disable-next-line security/detect-object-injection
    return Math.round((bytes / Math.pow(k, i)) * 100) / 100 + ' ' + sizes[i]
  }

  return (
    <Layout>
      <Head>
        <title>Upload content - Cross-check</title>
      </Head>

      <h1 className="govuk-heading-xl">Upload content files</h1>

          {!dataUsageAccepted ? (
            <DataUsageNotice
                onAccept={() => setDataUsageAccepted(true)}
                aiProviderName={aiProviderName}
                aiPrivacyPolicyUrl={aiPrivacyPolicyUrl}
              />
          ) : (
            <>
              {!storageInfo?.finalised && (
                <>
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

                  {success && (
                    <div
                      className="govuk-notification-banner govuk-notification-banner--success"
                      role="alert"
                    >
                      <div className="govuk-notification-banner__header">
                        <h2 className="govuk-notification-banner__title">Success</h2>
                      </div>
                      <div className="govuk-notification-banner__content">
                        <p className="govuk-body">{success}</p>
                      </div>
                    </div>
                  )}

                  {rejectedFiles.length > 0 && (
                    <div className="govuk-inset-text">
                      <h3 className="govuk-heading-s">
                        {rejectedFiles.length === 1
                          ? '1 file could not be uploaded'
                          : `${rejectedFiles.length} files could not be uploaded`}
                      </h3>
                      <p className="govuk-body-s">
                        Only PDF, DOCX, XLSX, PPTX, TXT, HTML, CSV and Markdown files are
                        supported. Hidden system files (such as .DS_Store) are skipped
                        automatically.
                      </p>
                      <ul className="govuk-list govuk-list--bullet govuk-body-s">
                        {rejectedFiles.map((f) => (
                          <li key={f.name}>
                            <strong>{f.name}</strong> — {f.reason}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  <h2 className="govuk-heading-m">Add files</h2>

                  <details className="govuk-details">
                    <summary className="govuk-details__summary">
                      <span className="govuk-details__summary-text">
                        How your files are processed
                      </span>
                    </summary>
                    <div className="govuk-details__text">
                      <p className="govuk-body">
                        When you upload a file, it is automatically converted to plain text
                        (Markdown format). Only this plain text is stored and analysed — your
                        original file is not kept.
                      </p>
                      <p className="govuk-body">
                        You can view the converted text for any uploaded file using the{' '}
                        <strong>View</strong> link in the file list.
                      </p>
                    </div>
                  </details>

                  <details className="govuk-details">
                    <summary className="govuk-details__summary">
                      <span className="govuk-details__summary-text">
                        HTML processing options
                      </span>
                    </summary>
                    <div className="govuk-details__text">
                      <p className="govuk-body-s">
                        These options apply to HTML files only.
                      </p>

                      <div className="govuk-form-group">
                        <div
                          className="govuk-checkboxes govuk-checkboxes--small"
                          data-module="govuk-checkboxes"
                        >
                          <div className="govuk-checkboxes__item">
                            <input
                              className="govuk-checkboxes__input"
                              id="strip-before-h1"
                              type="checkbox"
                              checked={stripBeforeH1}
                              onChange={(e) => setStripBeforeH1(e.target.checked)}
                            />
                            <label
                              className="govuk-label govuk-checkboxes__label"
                              htmlFor="strip-before-h1"
                            >
                              Remove everything before the first heading
                            </label>
                            <div className="govuk-hint govuk-checkboxes__hint">
                              Useful for stripping site navigation and banners from the top
                              of a page.
                            </div>
                          </div>
                        </div>
                      </div>

                      <div className="govuk-form-group">
                        <label className="govuk-label" htmlFor="footer-cutoff">
                          Remove from last occurrence of
                        </label>
                        <div className="govuk-hint" id="footer-cutoff-hint">
                          Everything including and after the last occurrence of this text will
                          be removed. Leave blank to keep all content. Useful for excluding
                          repeated footer content.
                        </div>
                        <input
                          className="govuk-input"
                          id="footer-cutoff"
                          type="text"
                          value={footerCutoff}
                          onChange={(e) => setFooterCutoff(e.target.value)}
                          maxLength={500}
                          aria-describedby="footer-cutoff-hint"
                          spellCheck={false}
                        />
                      </div>
                    </div>
                  </details>

                  <div className="govuk-form-group">
                    <label className="govuk-label" htmlFor="file-upload">
                      Upload files
                    </label>
                    <div className="govuk-hint">
                      Drag and drop files here, or click to browse. You can add files multiple
                      times.
                      <br />
                      Supported: PDF, DOCX, XLSX, PPTX, TXT, HTML, CSV, MD • Max 50MB per file
                    </div>
                    <div className="govuk-drop-zone" data-module="govuk-file-upload">
                      <input
                        ref={fileInputRef}
                        className="govuk-file-upload"
                        id="file-upload"
                        name="file-upload"
                        type="file"
                        multiple
                        onChange={handleFileChange}
                        disabled={uploading || !sessionId}
                        accept=".pdf,.docx,.xlsx,.pptx,.txt,.html,.csv,.md"
                      />
                    </div>
                  </div>

                  <div className="govuk-button-group">
                    <input
                      ref={folderInputRef}
                      id="folder-upload"
                      type="file"
                      multiple
                      {...({ webkitdirectory: '', directory: '' } as object)}
                      onChange={handleFileChange}
                      disabled={uploading || !sessionId}
                      style={{ display: 'none' }}
                    />
                    <button
                      type="button"
                      className="govuk-button govuk-button--secondary"
                      data-module="govuk-button"
                      onClick={() => folderInputRef.current?.click()}
                      disabled={uploading || !sessionId}
                    >
                      {uploading ? 'Uploading...' : 'Or add entire folder'}
                    </button>
                  </div>

                  {storageInfo && storageInfo.file_count > 0 && (
                    <div className="govuk-inset-text">
                      <p className="govuk-body-s">
                        Storage used:{' '}
                        <strong>{formatBytes(storageInfo.storage_used)}</strong> of{' '}
                        {formatBytes(storageInfo.storage_limit)}
                      </p>
                    </div>
                  )}

                  {expiresAt && (
                    <p className="govuk-body-s">
                      Your files will be automatically deleted on{' '}
                      <strong>{formatExpiry(expiresAt)}</strong>.
                    </p>
                  )}
                </>
              )}

              <FileList
                files={storageInfo?.files || []}
                onDelete={handleDeleteFile}
                onViewFile={handleViewFile}
                onClear={handleClearAll}
                onFinalize={handleFinalise}
                finalised={storageInfo?.finalised || false}
                formatBytes={formatBytes}
              />

              {modalFilename && (
                <MarkdownViewModal
                  filename={modalFilename}
                  content={modalContent}
                  loading={modalLoading}
                  error={modalError}
                  onClose={handleCloseModal}
                />
              )}
            </>
          )}
    </Layout>
  )
}
