import { clerkMiddleware, createRouteMatcher } from '@clerk/nextjs/server'
import { NextResponse } from 'next/server'
import { isEmailAllowed, parseAllowedDomains } from '@/lib/email-domain'

const isPublicRoute = createRouteMatcher(['/sign-in(.*)', '/sign-up(.*)', '/access-denied'])

const allowedDomains = parseAllowedDomains(process.env.ALLOWED_EMAIL_DOMAINS)

export default clerkMiddleware(async (auth, req) => {
  if (!process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY) return NextResponse.next()

  if (isPublicRoute(req)) return NextResponse.next()

  const { userId, sessionClaims } = await auth()

  if (!userId) {
    return NextResponse.redirect(
      new URL(`/sign-in?redirect_url=${encodeURIComponent(req.url)}`, req.url)
    )
  }

  if (allowedDomains.length > 0) {
    // Fail closed: if email is absent (e.g. not added to Clerk session token), deny access.
    const email = (sessionClaims as Record<string, unknown>)?.email as string | undefined
    if (!email || !isEmailAllowed(email, allowedDomains)) {
      return NextResponse.redirect(new URL('/access-denied', req.url))
    }
  }

  return NextResponse.next()
})

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico).*)'],
}
