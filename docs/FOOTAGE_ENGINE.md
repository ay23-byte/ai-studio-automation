# Documentary footage engine

How a scene gets a real, licensed video clip, how to check that the providers
work, and what the system will not do.

## Verification status

Measured by the **Live footage provider verification** workflow on GitHub
(run 43584838524, commit `2cacdb0`). For each provider it made a real search,
downloaded one real file and inspected it with ffprobe.

| Provider | Result | Evidence from the run |
|---|---|---|
| NASA | LIVE-VERIFIED | 1920x1080, 113 s, H.264 |
| Wikimedia Commons | LIVE-VERIFIED | 1920x1080, 16 s, VP9, CC0 |
| Internet Archive | LIVE-VERIFIED | 426x240, 38 s, H.264, public domain. Low resolution; see Limits. |
| Library of Congress | LIVE-VERIFIED | 1920x1080, 40 s, H.264, film published before the public-domain cutoff |
| Europeana | LIVE-VERIFIED | 720x576, 64 s, H.264, public domain |
| Pixabay | LIVE-VERIFIED | 1920x1080, 28 s, H.264 |
| Coverr | LIVE-VERIFIED | 1920x1080, 10 s, H.264; 3 AI-generated results refused |
| DVIDS | AUTH-FAILED | HTTP 403 with and without a Referer. The key is not accepted. See "DVIDS key". |
| US National Archives (NARA) | NO-ELIGIBLE-RESULTS | Search works (60 results). All refused by the rights rule. See "NARA". |
| Mixkit | MANUAL-IMPORT-ONLY | Never fetched automatically |

In the same run a six-scene selection got validated footage for 6 of 6 scenes,
from Pixabay (2), Wikimedia Commons (2), Coverr (1) and Library of Congress (1).

What this does not show: the result is one run, one clip per provider, from a
GitHub runner. In the three runs before it the Library of Congress answered
HTTP 429 and asked for a 20-minute wait, so it can be unavailable to a build.
No complete documentary has been rendered with this engine yet.

## Run the live verification

GitHub → Actions → **Live footage provider verification (search, download, ffprobe)** → Run workflow.

It needs no inputs. It also runs by itself on pull requests that change the
footage code. For each provider it searches, downloads one file, probes it, and
prints one of these statuses on the run page, with the counts and the reasons
results were refused:

| Status | Meaning | What to do |
|---|---|---|
| `LIVE-VERIFIED` | Search, download and ffprobe all succeeded | Nothing |
| `NOT-CONFIGURED` | The provider needs a key and none is set | Add the secret, or leave the provider off |
| `AUTH-FAILED` | The provider refused the key (401/403) | Check the key; for DVIDS see "DVIDS key" |
| `RATE-LIMITED` | The provider asked the client to slow down | Run again later |
| `UNREACHABLE` | Network error, timeout, server error or a block page | Run again; if it persists the provider is down or blocking the runner |
| `NO-ELIGIBLE-RESULTS` | Search worked; nothing passed the licence and relevance rules | Read the refusal reasons on the run page; try the `topic` input |
| `DOWNLOAD-FAILED` | Results found, no file could be fetched | Read `provider_report.md` in the artifact |
| `INVALID-MEDIA` | Files were fetched but none was playable video | Read `provider_report.md` |
| `MANUAL-IMPORT-ONLY` | Mixkit: never fetched automatically | See "Mixkit" below |

The `footage-live-verification` artifact holds `live_verification.md`,
`provider_report.md`, `licenses.csv`, `credits.txt`, the provenance record of
every fetched clip and one still frame per provider. The clips themselves are
deleted. Nothing is rendered, uploaded or published.

The same check runs locally: `python -m scripts.verify_footage_providers --selection`.

## Providers

| Provider | Key | Licence accepted | Notes |
|---|---|---|---|
| NASA Image and Video Library | none | NASA media guidelines, credit required | Searched only for space topics. Items naming a third party or a copyright holder are rejected. |
| Wikimedia Commons | none | CC0, public domain, CC BY, CC BY-SA, read per file | NC, ND and unlicensed files rejected. |
| Internet Archive | none | Only items carrying a CC BY, CC BY-SA, CC0 or public-domain licence URL | Items with no licence URL (most of the archive) are not used. |
| Library of Congress | none | "No known restrictions" plus a publication date old enough for the US public domain | Paced to one request every 3.2 s. Item records are requested only for films old enough to be used. The Library can still answer 429 and ask for a long wait; the provider is then skipped for the run. Home movies and other unpublished films are not used. |
| DVIDS | `DVIDS_API_KEY` | DVIDS API terms, credit required | See "DVIDS origin" below. |
| Europeana | `EUROPEANA_API_KEY` | Open rights statements only (CC0, PDM, CC BY, CC BY-SA, NoC-US) | Key sent in the `X-Api-Key` header. |
| US National Archives (NARA) | `NARA_API_KEY` | Use restriction "Unrestricted" **and** a public-domain or US-government-work statement in the record | See "NARA" below. |
| Pixabay | `PIXABAY_API_KEY` | Pixabay Content License | Stock; never used for a scene that asks for archival footage. |
| Coverr | `COVERR_API_KEY` | Coverr licence, credit "Video by <creator> on Coverr" | Stock; AI-generated items rejected; each use is reported to Coverr as its API terms require. |
| Mixkit | none | Mixkit Free License, confirmed by a person | Manual import only. |

### DVIDS key

DVIDS issues two kinds of key: one that must be sent with no Referer, and one
bound to a registered origin. The engine first sends the key with no Referer.
If DVIDS answers 403, it tries once more with `Referer: https://api.dvidshub.net`,
the value earlier versions always sent, and the report states which form worked.

In the live run DVIDS refused the key in both forms. To fix it:

1. In your DVIDS account, check that the API key is active and whether an
   origin is registered for it.
2. If an origin is registered, set the repository **variable**
   `DVIDS_API_REFERER` to exactly that origin.
3. Otherwise request a new key and store it as the `DVIDS_API_KEY` secret.
4. Run the live verification again.

Until then DVIDS supplies nothing and every build says so in its provider report.

### NARA

The NARA search works and its records are read correctly. In the live run, of
60 results: 25 had use restriction "Restricted - Possibly", 20 "Undetermined",
and 15 "Unrestricted". The 15 were also refused, because this project accepts a
NARA film only when the record additionally states public domain or US
government work. That rule existed before this change and was kept.

The effect is that NARA will rarely supply a clip. Accepting NARA's own
"Unrestricted" use status as sufficient would change that. It is a rights
decision for the channel owner, not a bug fix, so it was not made here.

### Mixkit

Mixkit has no public API and its terms forbid automated downloading. Download
the clip yourself, confirm its page shows the Free License, then:

```
python -m scripts.register_mixkit_asset clip.mp4 --title "Waves on a beach" \
    --source-url https://mixkit.co/free-stock-video/waves-on-a-beach-1234/ \
    --licence "Mixkit Free License" --tags "waves, beach" --confirm-free-license
```

or several at once from a JSON list (`file`, `title`, `source_url`, `licence`,
optional `creator`, `attribution`, `tags`):

```
python -m scripts.register_mixkit_asset --manifest clips/manifest.json --confirm-free-license
```

Each file is probed before it is accepted. Imported clips are tried first for
any scene whose words match their title and tags.

## How a clip is chosen

1. Operator-imported clips (Mixkit, Indian archives) are checked first.
2. Every configured provider is searched at the same time. One slow or failing
   provider does not hold up the others; it is left behind after its time budget.
3. Results are merged and duplicates removed (same provider id, or same file URL).
4. Each result must pass the licence policy and have a source page and licence URL.
5. Each result must share a subject word with the scene. Words such as
   "newsreel" or "historical film" do not count as a match.
6. Remaining results are ranked: 55% text relevance to the query, 15% match to
   the scene description, 18% declared resolution, 5% licence
   clarity, minus a penalty that grows with the share of scenes the same
   provider already supplied.
7. The best result is downloaded and probed. If the file is not valid video,
   the next result is tried (up to six).
8. A file identical to one already used in the documentary is refused.
9. If nothing passes, the scene is recorded as needing manual review and the
   renderer falls back to its existing generated or graphic visual. Unrelated
   footage is never substituted.

Text relevance is word matching against the provider's title, description and
tags. It is not a visual check. Every selected clip still needs a person to
confirm it shows what the narration says.

## Licence policy

One rule set (`core/footage_licence.py`) is used by discovery, the licence
register and the release check.

| State | Used? | Examples |
|---|---|---|
| `eligible` | yes | CC0, Public Domain Mark, Pixabay, confirmed Mixkit |
| `eligible_with_attribution` | yes, credit written to `credits.txt` | CC BY, CC BY-SA, NASA, DVIDS, Coverr, dated LOC items |
| `restricted` | no | In copyright, "all rights reserved", editorial or educational use only |
| `rejected` | no | CC NC, CC ND, third-party marker, AI-generated library content |
| `unknown` | no | No licence recorded, unrecognised licence name, name and URL disagree |

A provider's name alone never makes an asset usable. A result whose licence is
named but has no licence URL, or that has no source page, is an incomplete
record: it is not selected automatically.

## What each build records

- `<clip>.mp4.source.json`: provider, asset id, title, creator, source page,
  download URL, licence and its URL, licence state, credit text, size, duration,
  codec, SHA-256, download time, relevance score, ranking details.
- `provider_report.json` / `provider_report.md`: per provider, whether it was
  enabled, authentication and search status, HTTP status counts, results found,
  downloads attempted and succeeded, invalid files, licence rejections with the
  reason for each and a count, scenes supplied and the reason for any failure; per scene, what was selected or why
  nothing was. API keys are removed from everything written.
- `licenses.csv` and `credits.txt`: one row per used asset, including whether
  commercial use and modification are allowed, whether credit is required, the
  file hash and whether the file was validated.

The long-form review package copies the provider report into `evidence/`.

## Settings

All optional.

| Variable | Default | Effect |
|---|---|---|
| `FOOTAGE_MIN_RELEVANCE` | 0.25 | Lowest text relevance accepted for an archive result |
| `FOOTAGE_DIVERSITY_WEIGHT` | 0.25 | Strength of the penalty for reusing one provider |
| `FOOTAGE_MAX_DOWNLOAD_ATTEMPTS` | 6 | Files tried per scene before giving up |
| `FOOTAGE_MAX_CLIP_MB` | 250 | Largest file fetched for one scene |
| `FOOTAGE_MIN_WIDTH`, `FOOTAGE_MIN_HEIGHT` | 320, 240 | Smallest accepted picture |
| `FOOTAGE_MIN_DURATION_SECONDS` | 2 | Shortest accepted clip |
| `FOOTAGE_PROVIDER_SEARCH_SECONDS` | 60 | Time one provider gets per scene |
| `FOOTAGE_MAX_RETRY_AFTER_SECONDS` | 30 | Longest wait a provider may ask for before it is skipped for the run |
| `FOOTAGE_SEARCH_PAGES` | 1 (at most 3) | Result pages read per query |
| `FOOTAGE_DETAIL_LOOKUPS` | 3 (at most 8) | Search hits per query whose item record is fetched (Library of Congress, DVIDS, Internet Archive) |
| `DVIDS_API_REFERER` | unset | Origin registered with the DVIDS key |

## Limits

- DVIDS and NARA supply nothing at present (see the top of this page).
- Live verification is one clip per provider in one run. It is not a rendered documentary.
- The Library of Congress limits requests strictly and may be skipped for a whole run.
- Internet Archive files that carry a usable licence are often small. The clip
  verified live was 426x240. The minimum accepted size is 320x240; raise
  `FOOTAGE_MIN_WIDTH` and `FOOTAGE_MIN_HEIGHT` to refuse such files.
- Relevance is text matching, not image understanding.
- The engine cannot guarantee a real clip for every scene; scenes without one
  are listed in the provider report.
- Licence classification reads what the provider's API states. It is not legal
  advice, and privacy, publicity and trademark rights are outside it.
- Footage from broadcasters, YouTube or news agencies is never downloaded.
