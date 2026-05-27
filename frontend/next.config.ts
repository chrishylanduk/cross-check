import type { NextConfig } from 'next'

const apiBase = process.env.NEXT_PUBLIC_API_BASE || 'http://localhost:8000'

function clerkDomain(): string {
  const pk = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY
  if (!pk) return ''
  try {
    const raw = pk.split('_')[2]
    if (!raw) return ''
    const domain = Buffer.from(raw + '==', 'base64').toString('utf8').replace(/\$+$/, '')
    if (!domain || !/^[a-zA-Z0-9.-]+$/.test(domain)) return ''
    return domain
  } catch {
    return ''
  }
}

const clerk = clerkDomain()
const clerkSrc = clerk
  ? ` https://${clerk} https://clerk.${clerk} https://api.clerk.com`
  : ''

const turnstile = clerk ? ' https://challenges.cloudflare.com' : ''

const csp = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${clerkSrc}${turnstile}`,
  "style-src 'self' 'unsafe-inline'",
  `img-src 'self' data:${clerk ? ` https://${clerk}` : ''}`,
  "font-src 'self'",
  `connect-src 'self' ${apiBase}${clerkSrc}`,
  `frame-src${turnstile || " 'none'"}`,
  "object-src 'none'",
  "base-uri 'self'",
].join('; ')

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Enable standalone output for optimised Docker builds
  output: 'standalone',

  // Security headers
  async headers() {
    return [
      {
        source: '/:path*',
        headers: [
          {
            key: 'Content-Security-Policy',
            value: csp,
          },
          {
            key: 'X-DNS-Prefetch-Control',
            value: 'on',
          },
          {
            key: 'Strict-Transport-Security',
            value: 'max-age=63072000; includeSubDomains; preload',
          },
          {
            key: 'X-Content-Type-Options',
            value: 'nosniff',
          },
          {
            key: 'X-Frame-Options',
            value: 'DENY',
          },
          {
            key: 'X-XSS-Protection',
            value: '1; mode=block',
          },
          {
            key: 'Referrer-Policy',
            value: 'strict-origin-when-cross-origin',
          },
          {
            key: 'Permissions-Policy',
            value: 'camera=(), microphone=(), geolocation=()',
          },
        ],
      },
    ]
  },
}

export default nextConfig
