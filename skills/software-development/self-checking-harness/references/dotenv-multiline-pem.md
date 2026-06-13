# Dotenv Multiline PEM Keys

PEM keys in .env files need quoted heredoc format:
```bash
PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----\nMIIEpA...\n-----END RSA PRIVATE KEY-----"
```
Use `\n` escapes, not actual newlines.
