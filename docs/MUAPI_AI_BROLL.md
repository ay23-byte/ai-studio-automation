# Paid AI video providers disabled

The project now enforces a strict zero-paid policy by default.

- MuAPI and Replicate hosted video-generation requests are blocked whenever `FREE_ONLY_MODE` is unset or enabled.
- Cloudflare Workers AI image-generation requests are also blocked in that mode because usage beyond any free allowance can be billable.
- The long-form GitHub Actions workflow no longer exposes a paid MuAPI toggle or passes paid-provider credentials.
- Do not add paid-provider keys or enable metered inference in production workflows.

The adapter code remains in the repository for historical compatibility and isolated tests, but the production path is intentionally unavailable under the zero-paid policy. The free workflow should rely on public/free-to-access footage sources, open-source rendering tools, and non-metered local processing. A service advertising a free tier is not automatically safe to use: check whether it requires billing details, whether it can auto-charge, and whether usage is capped before adding it.

This policy does not make third-party footage rights automatic. Every asset still requires source, licence, attribution, and human editorial review.
