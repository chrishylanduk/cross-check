# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.0.x   | :white_check_mark: |

## Security Features

### Application Security

- **Hardened base images**: Uses Chainguard minimal container images
- **Rate limiting**: API endpoints are rate-limited to prevent abuse
- **Input validation**: File uploads are validated for size, type, and content
- **Session isolation**: User data is isolated per session
- **Security headers**: HSTS, CSP, X-Frame-Options, etc.
- **File sanitisation**: Filenames are sanitised to prevent path traversal
- **MIME type validation**: Files are validated by magic number, not just extension

### Infrastructure Security

- **Non-root containers**: All containers run as non-root users
- **Minimal capabilities**: Container capabilities are dropped except required ones
- **Read-only filesystem**: Frontend runs with read-only filesystem
- **No privilege escalation**: `no-new-privileges` security option enabled
- **Network isolation**: Services communicate via isolated Docker network

### Development Security

- **Pre-commit hooks**: Automated security and quality checks
- **Dependency scanning**: Regular updates and vulnerability scanning
- **Secrets management**: `.env` files are git-ignored

## Reporting a Vulnerability

If you discover a security vulnerability, please:

1. **Do not** open a public issue
2. Report via GitHub Security Advisories or email the repository maintainer
3. Include:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact
   - Any suggested fixes

You can expect:
- Acknowledgement within 48 hours
- Status update within 7 days
- Resolution timeline based on severity

## Security Best Practices for Deployment

### Environment Variables

- Never commit `.env` files
- Use strong, unique session secrets
- Restrict CORS origins to your actual domains
- Set `ENVIRONMENT=production` in production

### Storage

- Files are stored on ephemeral disk (container filesystem)
- Session-based storage, maximum 1 hour per session
- Automatic cleanup when sessions expire
- Orphaned files cleaned up on server startup (2-hour threshold)
- No persistent volumes used
- Consider implementing external storage (S3, database) if long-term persistence is needed

### Network

- Deploy behind a reverse proxy (nginx, Cloudflare, etc.)
- Use HTTPS/TLS in production
- Enable firewall rules to restrict access
- Consider using a WAF (Web Application Firewall)

### Monitoring

- Monitor rate limit violations
- Set up alerts for failed authentication attempts
- Review logs regularly for suspicious activity
- Enable security event logging

### Updates

- Keep dependencies up to date
- Monitor security advisories for:
  - Python/FastAPI ecosystem
  - Node.js/Next.js ecosystem
  - Chainguard base images
- Apply security patches promptly

## Additional Considerations

For production deployments with sensitive data, consider:
- Implementing user authentication and authorization
- Adding encryption for data at rest and in transit
- Setting up comprehensive audit logging
- Configuring persistent storage solutions
- Implementing regular security audits and penetration testing
