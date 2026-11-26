# Security

Please report vulnerabilities privately to the maintainers rather than in a public issue.

What the app protects, and how:

- **Passwords:** scrypt (N=2^14, r=8, p=1) with a per-user salt; constant-time comparison; login
  attempts rate-limited per IP and email.
- **Sessions:** random 256-bit tokens in an HttpOnly, SameSite=Lax cookie (Secure in production); only
  the SHA-256 of the token is stored. Changing your password signs out other sessions.
- **CSRF:** state-changing requests need a custom header plus the per-session CSRF token.
- **API keys:** encrypted at rest with Fernet using a key derived from `CARSCAN_SECRET_KEY`; never
  returned to the browser (only the last 4 characters).
- **Uploads:** decoded and re-encoded server-side (which strips EXIF/GPS), size-limited, stored under
  random names, and served only to their owner.
- **Isolation:** every car, scan, run, photo, conversation and pending action is checked against the
  signed-in user.
- **Browser:** strict Content-Security-Policy (no inline scripts), `X-Frame-Options: DENY`, `nosniff`,
  and HTML escaped by default in the frontend templating.
- **The car:** the assistant can never clear codes or send raw commands. Clearing always needs the user
  to type `CLEAR`.
