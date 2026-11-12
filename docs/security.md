# Security model and limitations

Passwords are salted and hashed with scrypt. Sessions are random bearer tokens in HttpOnly, SameSite=Lax cookies. Mutating authenticated forms require a CSRF token. SQL queries use parameters; displayed dynamic text is HTML-escaped. Role checks restrict admin and support routes. The HTTP server binds to localhost only.

**Not production-ready:** no HTTPS, persistent session store, brute-force throttling, TOTP, external security review, secure cookie flag (local HTTP), card payment integration, or real-world compliance. Only use fictional data and localhost. The preloaded passwords are intentionally public demo credentials.
