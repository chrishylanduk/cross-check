import { useState } from 'react'

interface DataUsageNoticeProps {
  onAccept: () => void
  aiProviderName: string
  aiPrivacyPolicyUrl: string
}

export default function DataUsageNotice({ onAccept, aiProviderName, aiPrivacyPolicyUrl }: DataUsageNoticeProps) {
  const [agreed, setAgreed] = useState(false)

  const handleAccept = () => {
    if (agreed) {
      sessionStorage.setItem('data-usage-accepted', 'true')
      onAccept()
    }
  }

  return (
    <div className="govuk-notification-banner" role="region" aria-labelledby="data-usage-title">
      <div className="govuk-notification-banner__header">
        <h2 className="govuk-notification-banner__title" id="data-usage-title">
          Before you upload content
        </h2>
      </div>
      <div className="govuk-notification-banner__content">
        <h3 className="govuk-heading-m">How your content is processed</h3>

        <p className="govuk-body">
          Your content is sent to{' '}
          {aiPrivacyPolicyUrl ? (
            <a
              className="govuk-link"
              href={aiPrivacyPolicyUrl}
              rel="noreferrer noopener"
              target="_blank"
            >
              {aiProviderName}
            </a>
          ) : (
            aiProviderName
          )}{' '}
          via its API for analysis.{' '}
          {aiPrivacyPolicyUrl && (
            <>
              Read {aiProviderName}&rsquo;s{' '}
              <a
                className="govuk-link"
                href={aiPrivacyPolicyUrl}
                rel="noreferrer noopener"
                target="_blank"
              >
                privacy notice
              </a>{' '}
              for full details.
            </>
          )}
        </p>

        <p className="govuk-body">
          Your files are stored temporarily on our servers for up to 24 hours, then automatically
          deleted. We do not keep copies or share your content with anyone else.
        </p>

        <p className="govuk-body">
          If you need to keep your content on your own infrastructure, the{' '}
          <a className="govuk-link" href="https://github.com/chrishylanduk/cross-check" rel="noreferrer noopener" target="_blank">
            source code is open source
          </a>{' '}
          and can be self-hosted with your own AI provider.
        </p>

        <h3 className="govuk-heading-m">Before you continue, confirm that</h3>

        <ul className="govuk-list govuk-list--bullet">
          <li>you have the right to share this content for analysis</li>
          <li>the content does not include illegal material or malicious code</li>
          <li>you understand it will be sent to {aiProviderName} for processing</li>
        </ul>

        <div className="govuk-form-group" style={{ marginTop: '20px' }}>
          <div className="govuk-checkboxes">
            <div className="govuk-checkboxes__item">
              <input
                className="govuk-checkboxes__input"
                id="agree"
                name="agree"
                type="checkbox"
                checked={agreed}
                onChange={(e) => setAgreed(e.target.checked)}
              />
              <label className="govuk-label govuk-checkboxes__label" htmlFor="agree">
                I confirm I have read and understood the above
              </label>
            </div>
          </div>
        </div>

        <button
          className="govuk-button"
          data-module="govuk-button"
          onClick={handleAccept}
          disabled={!agreed}
        >
          Accept and continue
        </button>
      </div>
    </div>
  )
}
