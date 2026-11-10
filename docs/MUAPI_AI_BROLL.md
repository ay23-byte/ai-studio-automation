# Hosted paid AI providers disabled

The project enforces a strict zero-paid policy. The following hosted inference providers are not available in production and cannot be enabled with environment variables or API keys:

- MuAPI text-to-video: the network adapter has been removed; the entry point always returns disabled.
- Replicate text-to-video: the production entry point has been removed and always returns disabled.
- Cloudflare Workers AI image generation: the request function always returns disabled because usage can become billable after free allowances.

The long-form GitHub Actions workflow does not expose a paid MuAPI toggle or pass paid-provider credentials. Do not reintroduce paid hosted inference providers.

The free production path uses publicly accessible footage/image sources where their terms permit, open-source rendering tools such as FFmpeg, and non-metered processing. Free tiers that require billing details, can auto-charge, or have unclear overage behavior are not considered safe for this project.

This policy does not make third-party footage rights automatic. Every asset still requires source, licence, attribution, and human editorial review.
