# Security policy

Noble Search stores sensitive things: your API keys, email app password, resume and career history. Please report security problems **privately**.

## How to report

- Use **Security → Report a vulnerability** on this repository (GitHub private vulnerability reporting). Do **not** open a public issue.
- Include what you found, how to reproduce it, and what an attacker could do with it.
- You'll get an answer within 7 days. Fixes for confirmed problems are released as quickly as possible, and you'll be credited unless you prefer otherwise.

## Supported versions

Only the latest release and the `main` branch get security fixes. Update with `./scripts/update.sh`.

## Good practice for users

- Keep `BIND_ADDRESS=127.0.0.1` (the default) unless you need access from other devices. If you change it, set `APP_PASSWORD`, and prefer Tailscale over opening your network.
- **Never** forward port 8093 on your router to the internet.
- Set a monthly spending limit on your Claude API account.
- Back up the `data/` folder privately. It contains your keys and your resume.
