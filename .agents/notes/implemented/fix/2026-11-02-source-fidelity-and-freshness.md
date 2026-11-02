# Agent Note: preserve raw source and read current evidence

Status: implemented

## Problem

The configured extractor removed angle-bracket includes and escaped identifiers when fetching a raw C header. Separately, a latest-release query returned stale search snippets, and research could spend its remaining rounds on unrelated library API examples. Retrieval success hid the difference between a discovered excerpt and a current page that had actually been read.

## Decision

Recognized raw text uses a bounded direct transport with reversible decoding and a response-body hash. Public-address validation, pinned connections and per-hop redirect checks keep this new local network path separate from provider credentials. Lossy page extraction is not a valid fallback when source fidelity is required.

Time-sensitive search retains its selected discovery channel, then reads a small number of observed URLs within the existing deadline. Release facts are distinct from API usage, so Context7 is excluded from that intent. Jev sees the original cutoff and only read evidence when judging freshness sufficiency. Missing evidence remains explicit rather than becoming a confident latest-version claim.

## Alternatives considered

- Switching the first extractor merely changes which conversion can damage code; it does not establish fidelity.
- Forcing every current query through the main search model adds cost and bypasses the user's automatic-routing preference.
- Hardcoding a product's download URL or version would fit one benchmark while failing unrelated products and future releases.
- Running unrestricted follow-up discovery for every ordinary query would replace the lightweight search contract with a research loop.

## Consequences

Raw reads expose complete-body metadata and fail explicitly when the body cannot be preserved. Current queries may make additional read and Jev calls, increasing latency and provider usage. The page/time limits keep that cost bounded. Non-current searches keep their previous behavior. A sufficient Jev assessment is an evidence judgment, not a guarantee of truth or future freshness. The operational contract is recorded in `codestable/spec/retrieval-integrity.md`.

Live validation exposed two integration gaps that mocks alone did not catch. Ignoring the existing local proxy made direct reads time out; forwarding the pinned IP through an HTTP CONNECT proxy then exposed the transport's origin-SNI limitation. An origin-only SSL context now preserves certificate hostname verification across that tunnel, without changing the separate proxy TLS context. Research's existing first-360-character report also hid the newly retrieved facts behind page navigation; time-sensitive reports now select relevant dated/versioned passages, preserving full evidence separately. The failed live observations are retained alongside the final rerun.
