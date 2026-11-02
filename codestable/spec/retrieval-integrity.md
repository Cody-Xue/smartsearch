# Source fidelity and current information

`fetch` recognizes raw source hosts and text-file URLs before using page extractors. The direct transport returns the complete response body without Markdown conversion or whitespace normalization. `content.encode(encoding)` reconstructs the body whose SHA-256 is reported; `hash_scope=decoded_response_body` means after HTTP content decoding, not compressed wire bytes. Raw text is limited to 2 MiB and 30 seconds within the caller's total deadline. A failed raw read returns an error rather than a lossy extractor result. Ordinary HTML and repository file-view pages retain the configured extraction chain.

Direct reads accept public HTTP(S) destinations, validate every redirect, pin the validated IP while retaining the original Host and TLS server name, and send no provider credentials or ambient cookies. Binary/error HTML, undecodable text, partial HTTP responses and over-limit bodies cannot be reported as complete source files.

The user's existing environment/system proxy is honored, with proxy bypass checked against the original hostname. A proxy tunnel still targets the validated IP. Origin TLS verifies the original hostname, while HTTPS proxy TLS uses a separate context; certificate verification is never disabled. Proxy authentication is confined to the proxy transport.

For successful raw reads, `fetch --format content --output PATH` writes the original body bytes using the returned encoding. Redirected binary stdout follows the same rule, with no added terminator. JSON preserves the content string and encoding; Markdown is a display format. Optional research evidence filtering must not turn a full-body checksum into an excerpt checksum claim.

`search` still uses Jev or local rules to choose one discovery channel. For latest/current/as-of questions, it additionally reads a bounded set of discovered pages: up to three for release facts and two for other current facts. The read phase shares the total deadline and uses at most 45 seconds or half the remaining budget. Candidates come from search results or links present in read pages; paths and release answers are not invented. The original question and requested cutoff accompany Jev's selection and assessment.

Context7 remains available for library/API usage, but not as a release-status or unrelated current-news source. Time-sensitive assessments use read content, with `freshness_check` describing reads and `evidence_assessment` describing sufficiency. Search API success alone does not establish freshness, authority or correctness. Missing evidence stays visible through gaps and warnings. Non-current ordinary search remains single-pass and unassessed; `research` retains its bounded loop and reuses the freshness reads.

Explicit stable-only questions exclude candidate links clearly labeled prerelease in their own title, URL or row. Comparisons requesting stable and prerelease versions keep both. Time-sensitive research reports show selected dated/versioned passages rather than only the beginning of a page; full evidence bodies remain available in `evidence_items`.

Provider allowlists, disabled providers, health gates and total deadlines still apply. These changes improve retrieval evidence; they do not guarantee that a fetched page is up to date or that Jev's judgment is correct.
