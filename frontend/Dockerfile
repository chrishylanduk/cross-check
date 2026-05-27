# Multi-stage build for Next.js frontend
FROM node:22-slim AS builder

WORKDIR /app

# Copy package files
COPY package*.json ./

# Install dependencies
RUN npm ci

# Copy application code
COPY . .

# NEXT_PUBLIC_* vars are inlined at build time — declare as ARGs so Railway can pass them in
ARG NEXT_PUBLIC_API_BASE
ARG NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY
ENV NEXT_PUBLIC_API_BASE=$NEXT_PUBLIC_API_BASE
ENV NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=$NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY

# Build application in standalone mode
RUN npm run build

# Production stage
FROM node:22-slim

WORKDIR /app

# Copy standalone build output
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public

# Set environment variables
ENV NODE_ENV=production \
    HOSTNAME=0.0.0.0

# Health check (uses PORT env var set by Railway)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["node", "-e", "require('http').get('http://localhost:' + (process.env.PORT || 3000), (r) => process.exit(r.statusCode === 200 ? 0 : 1))"]

# node:22-slim includes a built-in non-root 'node' user
USER node

# Run the application
CMD ["node", "server.js"]
