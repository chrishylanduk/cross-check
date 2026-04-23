import { useState } from 'react'

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || 'http://localhost:8000'

interface PrototypePasswordModalProps {
  onSuccess: (token: string) => void
}

export default function PrototypePasswordModal({ onSuccess }: PrototypePasswordModalProps) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    setError(null)
    setLoading(true)

    try {
      const response = await fetch(`${API_BASE}/api/auth/validate`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ password }),
      })

      const data = await response.json()

      if (response.ok && data.valid) {
        onSuccess(data.token)
      } else {
        setError('Incorrect password. Please try again.')
        setPassword('')
      }
    } catch {
      setError('Unable to connect to the service. Please try again.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div
      style={{
        position: 'fixed',
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        backgroundColor: '#f3f2f1',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 9999,
      }}
    >
      <div
        style={{
          maxWidth: '500px',
          width: '100%',
          padding: '40px',
          backgroundColor: 'white',
          border: '2px solid #0b0c0c',
        }}
      >
        <h1 className="govuk-heading-l" style={{ marginTop: 0 }}>
          Cross-check
        </h1>
        <p className="govuk-body">
          This is a prototype service. Please enter the password to continue.
        </p>

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

        <form onSubmit={handleSubmit}>
          <div className="govuk-form-group">
            <label className="govuk-label govuk-label--m" htmlFor="password">
              Password
            </label>
            <input
              className="govuk-input"
              id="password"
              name="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={loading}
              autoComplete="off"
              autoFocus
            />
          </div>
          <button
            type="submit"
            className="govuk-button"
            data-module="govuk-button"
            disabled={loading || !password}
          >
            {loading ? 'Checking...' : 'Continue'}
          </button>
        </form>
      </div>
    </div>
  )
}
