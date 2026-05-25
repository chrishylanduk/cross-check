import { describe, it, expect } from 'vitest'
import { isEmailAllowed, parseAllowedDomains } from '@/lib/email-domain'

describe('parseAllowedDomains', () => {
  it('returns empty array for empty string', () => {
    expect(parseAllowedDomains('')).toEqual([])
  })

  it('returns empty array for undefined', () => {
    expect(parseAllowedDomains(undefined)).toEqual([])
  })

  it('trims whitespace and lowercases entries', () => {
    expect(parseAllowedDomains(' Example.COM , Beta.org ')).toEqual(['example.com', 'beta.org'])
  })

  it('filters empty entries from trailing commas', () => {
    expect(parseAllowedDomains('example.com,')).toEqual(['example.com'])
  })
})

describe('isEmailAllowed', () => {
  describe('empty allowlist', () => {
    it('permits any email', () => {
      expect(isEmailAllowed('anyone@anything.com', [])).toBe(true)
    })
  })

  describe('bare domain entry (exact + subdomains)', () => {
    const domains = ['example.com']

    it('permits exact domain match', () => {
      expect(isEmailAllowed('user@example.com', domains)).toBe(true)
    })

    it('permits subdomain', () => {
      expect(isEmailAllowed('user@sub.example.com', domains)).toBe(true)
    })

    it('permits deep subdomain', () => {
      expect(isEmailAllowed('user@a.b.example.com', domains)).toBe(true)
    })

    it('blocks unrelated domain', () => {
      expect(isEmailAllowed('user@notexample.com', domains)).toBe(false)
    })

    it('blocks domain that merely ends with the allowed string but not as a subdomain', () => {
      expect(isEmailAllowed('user@evil-example.com', domains)).toBe(false)
    })

    it('blocks completely different domain', () => {
      expect(isEmailAllowed('user@attacker.org', domains)).toBe(false)
    })
  })

  describe('@ prefix (exact domain only)', () => {
    const domains = ['@example.com']

    it('permits exact domain', () => {
      expect(isEmailAllowed('user@example.com', domains)).toBe(true)
    })

    it('blocks subdomain', () => {
      expect(isEmailAllowed('user@sub.example.com', domains)).toBe(false)
    })

    it('blocks unrelated domain', () => {
      expect(isEmailAllowed('user@other.com', domains)).toBe(false)
    })
  })

  describe('multiple domains', () => {
    const domains = ['alpha.com', '@beta.com']

    it('permits email matching first entry', () => {
      expect(isEmailAllowed('user@alpha.com', domains)).toBe(true)
    })

    it('permits subdomain of bare entry', () => {
      expect(isEmailAllowed('user@sub.alpha.com', domains)).toBe(true)
    })

    it('permits email matching second entry (exact)', () => {
      expect(isEmailAllowed('user@beta.com', domains)).toBe(true)
    })

    it('blocks subdomain of @ entry', () => {
      expect(isEmailAllowed('user@sub.beta.com', domains)).toBe(false)
    })

    it('blocks email matching neither entry', () => {
      expect(isEmailAllowed('user@gamma.com', domains)).toBe(false)
    })
  })

  describe('edge cases', () => {
    it('is case-insensitive', () => {
      expect(isEmailAllowed('User@EXAMPLE.COM', ['example.com'])).toBe(true)
    })

    it('uses rightmost @ segment as domain (multiple @ signs)', () => {
      // attacker@evil.com@allowed.com — domain resolves to allowed.com
      expect(isEmailAllowed('attacker@evil.com@allowed.com', ['@allowed.com'])).toBe(true)
    })

    it('blocks email with no @ sign', () => {
      expect(isEmailAllowed('nodomain', ['example.com'])).toBe(false)
    })

    it('blocks empty email', () => {
      expect(isEmailAllowed('', ['example.com'])).toBe(false)
    })

    it('fail closed: missing email (empty string) denied when allowlist set', () => {
      expect(isEmailAllowed('', ['example.com'])).toBe(false)
    })
  })
})
