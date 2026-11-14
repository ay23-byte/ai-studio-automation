# Footage engine defect report

Audit of the footage code at commit `237db51` and what replaced it. Each defect
was found by reading the code against the provider's published API
documentation, or by a test that failed. None was found by a live run, because
the development sandbox cannot reach the provider hosts. Whether the fixes work
against the real services is unproven until the live verification workflow is
run (see `docs/FOOTAGE_ENGINE.md`).

## Why documentaries had little real footage

| # | Defect | Effect | Fix |
|---|---|---|---|
| 1 | Archives were searched one after another; stock libraries only if every archive failed | Slow, and the first provider with any result won | All providers searched at once and ranked as one pool (`core/footage_engine.py`) |
| 2 | Two different licence rule sets; the one used during discovery never accepted DVIDS or Library of Congress | Those providers could never supply a clip | One policy, `core/footage_licence.py`, used by discovery, register and release check |
| 3 | NASA: file lists use `http://` URLs, which the HTTPS check rejected; source page left empty | NASA results were discarded | URLs upgraded to HTTPS; details page recorded |
| 4 | Wikimedia: file URLs end in `?utm_source=…`, which failed the file-type check | Every Wikimedia result was dropped | Extension read from the URL path |
| 5 | Internet Archive: licence version hard-coded to 4.0; the older `licenses/publicdomain/` URL not recognised; file names with `#` or folders not encoded | Wrong licence recorded, valid items rejected, downloads broken | Licence read from the item's own URL; names encoded |
| 6 | Library of Congress: rights read from search results, which do not carry them; no request pacing | Everything rejected; risk of the one-hour block LOC applies above 20 requests a minute | Rights read from the item record; one request every 3.2 seconds |
| 7 | DVIDS: expected file URLs in search results; they come from a second call. A `Referer` of the API's own host was always sent | No DVIDS clip could be downloaded | Asset call added; `Referer` sent only when `DVIDS_API_REFERER` is set |
| 8 | Europeana: key sent as the deprecated `wskey` parameter; no open-rights filter; Public Domain Mark rejected | Few or no results | `X-Api-Key` header, `reusability=open`, PDM accepted |
| 9 | Coverr: undocumented `x-api-key` header; wrong size fields; no credit; no download report; AI-generated flag ignored | Authentication likely failed; licence terms not met | `Authorization: Bearer`, live field names, creator credit, download report, AI items rejected |
| 10 | Pixabay: rate-limit headers ignored; a refused key was retried on every scene | Wasted requests and avoidable throttling | Reset time honoured; provider switched off for the run after a refusal |
| 11 | NARA: page size sent as `rows`, which is not a documented parameter (the name is `limit`) | Unknown; an undocumented parameter may be ignored or refused | `limit` used |
| 12 | File check returned only true or false and did not decode the file | A truncated download with an intact header passed; failures had no reason | `core/footage_validation.py`: named status per failure; the end of the file is decoded |
| 13 | Duplicates detected only by provider id | The same film from two providers, or under two ids, could appear twice | Also compared by file URL and by SHA-256 of the downloaded file |
| 14 | No provider-diversity logic | One provider could supply every clip | Ranking penalty proportional to the provider's share so far |
| 15 | No report of what each provider did | Nobody could tell why a build had no footage | `provider_report.json` / `.md` in every build |
| 16 | The provider health check passed if any one provider returned any result, and downloaded nothing | "Healthy" said nothing about most providers | Search check now names each provider's status; new workflow downloads and probes a file per provider |
| 17 | Mixkit import took one file with no validation | A web page saved as `.mp4` could be registered | ffprobe check; manifest import |
| 18 | Licence register had no commercial-use, modification or file-hash columns | Reviewers could not see the obligations | Columns added; `credits.txt` lists required credits first |

## Defects found by the new tests

| # | Defect | Fix |
|---|---|---|
| 19 | Archival queries are expanded with words such as "newsreel". A result sharing only that word was accepted: "night traffic newsreel" selected "Harbour ships newsreel". Present in the old code too. | A result must share a subject word with the query |
| 20 | A licence name of "All rights reserved" beside a Creative Commons URL was classified from the URL and accepted. Introduced during this rewrite and caught by the existing release test before commit. | A restrictive name wins; a name and URL that disagree are not accepted |
| 21 | An unreachable stock provider was retried three times for every query of every scene | Switched off for the run after two failed searches |
| 22 | A provider that failed was searched again for every query of the scene | Dropped for the scene after two consecutive errors |
| 23 | The review manifest did not carry the dated public-domain basis of a Library of Congress clip or the operator's Mixkit confirmation, so the release check would have refused clips the engine had correctly selected. Introduced during this rewrite and caught by the full test run before commit. | Both are copied into the manifest when the build recorded them; a test runs every provider's record through the release check |

## Behaviour changes to be aware of

- **DVIDS `Referer`.** The fixed header is gone. If the key worked only because
  of it, DVIDS will answer 403 and the report will say so. Setting the
  repository variable `DVIDS_API_REFERER` to `https://api.dvidshub.net` restores
  the old request exactly.
- **Stricter acceptance.** Internet Archive items without a licence URL,
  Library of Congress items without a dated public-domain basis, results
  without a source page, and files that fail decoding are no longer used. Some
  scenes that previously received a clip will now fall back to a generated or
  graphic visual and be listed for manual review.
- **Coverr credit.** Coverr clips now require the credit line its free API tier
  asks for, so they appear in `credits.txt`.

## Tests changed on purpose

- `tests/test_coverr_adapter.py`: the assertion on the `x-api-key` header now
  expects `Authorization: Bearer`.
- `tests/test_documentary_source_adapters.py`: provider fixtures rewritten to
  the documented response shapes; new cases for LOC rights, Wikimedia NC and
  unlicensed files, Internet Archive without a licence URL, the DVIDS origin
  and the NARA parameters; the Mixkit test uses a real clip.
- `tests/test_asset_licensing.py`: the unknown-licence message is now
  descriptive, so the test checks for its key phrase.
- `tests/test_runtime_limits.py`: the import guard accepts
  `from core.downloads import DownloadError, fetch`.
- `tests/test_archival_video.py`: three tests set the NARA and Europeana keys,
  because providers without a key are no longer called.

No test was removed and no assertion was loosened to make a failure pass.

## Not verified

- No request has reached a real provider from this code. NARA's response shape
  in particular is taken from the previous code, not from documentation; the
  published description confirms only the endpoint, the header and the
  parameter names.
- The offline tests prove that the code handles the documented response shapes
  and failure modes. They cannot prove that a provider accepts the keys, that
  the runner is not blocked, or that real files validate.
