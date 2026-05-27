import Head from 'next/head'
import Layout from '@/components/Layout'

interface AnalysisOption {
  title: string
  description: string
  href: string
  available: boolean
}

const options: AnalysisOption[] = [
  {
    title: 'Compliance',
    description: 'Check whether your content follows your organisation\'s content guidelines and style guides.',
    href: '/analyse/compliance',
    available: true,
  },
  {
    title: 'Consistency',
    description: 'Find where documents contradict each other or cover the same topic inconsistently.',
    href: '/analyse/inconsistencies',
    available: true,
  },
  {
    title: 'Completeness',
    description: 'Identify content gaps based on questions your users are asking.',
    href: '/analyse/completeness',
    available: false,
  },
]

export default function Analyse() {
  return (
    <Layout showSessionBanner>
      <Head>
        <title>Choose analysis type - Cross-check</title>
      </Head>

      <h1 className="govuk-heading-xl">Choose type of analysis</h1>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '16px', maxWidth: '640px' }}>
        {options.map((option) => (
          <div
            key={option.title}
            style={{
              border: '1px solid #b1b4b6',
              padding: '20px 20px 16px',
              opacity: option.available ? 1 : 0.6,
            }}
          >
            <h2 className="govuk-heading-m" style={{ marginBottom: '8px' }}>
              {option.title}
              {!option.available && (
                <strong
                  className="govuk-tag govuk-tag--grey"
                  style={{ marginLeft: '12px', verticalAlign: 'middle', fontSize: '14px' }}
                >
                  Coming soon
                </strong>
              )}
            </h2>
            <p className="govuk-body">{option.description}</p>
            {option.available ? (
              <a href={option.href} className="govuk-button" style={{ marginBottom: 0 }}>
                Start
              </a>
            ) : (
              <button type="button" className="govuk-button" disabled style={{ marginBottom: 0 }}>
                Start
              </button>
            )}
          </div>
        ))}
      </div>
    </Layout>
  )
}
