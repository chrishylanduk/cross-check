/**
 * Returns true if the email's domain is permitted by the allowlist.
 *
 * Rules match the backend (_is_email_domain_allowed in main.py) — keep in sync.
 *   - Empty allowlist → all domains permitted.
 *   - '@example.com'  → exact domain only (no subdomains).
 *   - 'example.org'   → exact domain + any subdomain (sub.example.org, etc.).
 */
export function isEmailAllowed(email: string, allowedDomains: string[]): boolean {
  if (allowedDomains.length === 0) return true
  const domain = email.split('@').pop()?.toLowerCase() ?? ''
  return allowedDomains.some((allowed) =>
    allowed.startsWith('@')
      ? domain === allowed.slice(1)
      : domain === allowed || domain.endsWith('.' + allowed)
  )
}

/**
 * Parses the ALLOWED_EMAIL_DOMAINS env var into a normalised list.
 */
export function parseAllowedDomains(raw: string | undefined): string[] {
  return (raw ?? '')
    .split(',')
    .map((d) => d.trim().toLowerCase())
    .filter(Boolean)
}
