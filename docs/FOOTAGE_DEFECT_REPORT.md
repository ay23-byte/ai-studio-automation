# Footage engine defect report

Audit of the footage code at commit `237db51` and what replaced it. Defects 1
to 23 were found by reading the code against each provider's published API
documentation, or by a test that failed. Defects 24 to 29 were found by running
the code against the real services on GitHub (see `docs/FOOTAGE_ENGINE.md` for
the results of that run).

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

## Defects found by the live runs and by CI

| # | Defect | Fix |
|---|---|---|
| 24 | Library of Congress: the rights text attached to every film of a collection mentions "copyrighted motion pictures" in "rare cases". It was read as a restriction on the item, so every film was refused. The offline test used a shortened rights text and did not catch it. | Only a sentence about the item itself is a bar. The test fixture now holds the Library's text word for word. |
| 25 | Library of Congress: item records were requested for films that could never be used (no publication date before the public-domain cutoff), spending the few requests the Library allows. An item record that could not be fetched was counted as a licence refusal. | Such films are skipped before any item request; an unreadable record is reported as that. |
| 26 | Library of Congress: a home movie dated before the cutoff would have been treated as public domain by date, although it was never published. | Home movies and unpublished films get no date basis and are not used. |
| 27 | Provider text held in nested lists was stored as a Python list literal (`['…\r\n…']`) in the rights statement. | Nested lists are flattened to plain text. |
| 28 | When a download failed, only the last file tried was reported, as a raw command line. A refusal gave no reason, so "60 refused" for NARA could not be explained. | Every file tried is named with its reason; every refused result is counted under a reason. |
| 29 | The new tests build real clips with FFmpeg, and the `unit-tests` job has no FFmpeg, so that job failed on the pull request. | Those tests skip without FFmpeg and run in the `ffmpeg-smoke` job, which prints any skip. |

One regression was introduced and fixed during this work: a recognised licence
name without a licence URL was classed `unknown`, which made the release check
refuse sources a reviewer is allowed to complete. It is now an incomplete
record: not selected automatically, and still completable at review.

## Behaviour changes to be aware of

- **DVIDS `Referer`.** The key is first sent with no Referer. After a 403 it is
  sent once with the earlier fixed `Referer: https://api.dvidshub.net`, so a key
  that worked before still works, and the report says which form was accepted.
  In the live run the key was refused in both forms.
- **Library of Congress.** Fewer requests: item records are fetched only for
  films old enough to be used.
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

- `tests/test_footage_engine.py` is new in this work and was revised as defects
  were found. Two table cases that expected `unknown` for a named licence
  without a URL were replaced by a dedicated test of the corrected rule. One
  assertion now expects two DVIDS requests (one per key form) instead of one.
  Tests that need FFmpeg skip when it is absent.

No test in an older file was removed, and no assertion was loosened to make a
failure pass.

## Not verified

- No complete documentary has been rendered with this engine. The live run
  checks one clip per provider and a six-scene selection.
- DVIDS: nothing beyond the refusal is known, because the key was not accepted.
  The adapter's handling of real DVIDS answers is untested.
- NARA: the search and the reading of use restrictions are confirmed by the
  live run. No NARA file has been downloaded, because none passed the rights rule.
- Live results are from one day on GitHub runners. Providers change, and the
  Library of Congress answered HTTP 429 in three of the four runs.
- Nobody has looked at the downloaded clips. Relevance is word matching against
  the provider's text; whether a clip shows what its title says was not checked.
