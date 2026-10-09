# ADR-009: Live web search for the fact check

Status: accepted (2026-09-26, confirmed 2026-09-27 in `DEBATE-LOG.md`, point 5). Owners: the project owner and Claude.

## Context

The owner proposed a live Google query in a headless browser. We found these problems:

1. Automated queries on the Google results page are against the Google terms of service. Google blocks them with bot detection and CAPTCHAs. We do not solve CAPTCHAs, and we do not build tools that avoid bot detection.
2. Google does not accept new customers for the official Custom Search JSON API. The API ends on 2027-01-01.
3. Vertex AI Search searches our own content, not the web.

## Options

| Option | Cost | Notes |
|---|---|---|
| Brave Search API | 5 USD for 1,000 requests, 5 USD credit each month | Independent index. Has an LLM-context endpoint. |
| Tavily | 1,000 free credits each month, then 0.008 USD for each credit | Made for agents. Returns clean page text. |
| SerpAPI | paid | Returns Google results through its own service. Use it only if the owner needs Google results in particular. |
| Scrape Google with a headless browser | free | Rejected (terms of service, CAPTCHAs). |

## Decision

1. Define one interface: `SearchProvider.search(query, max_results, recency_days) -> list[Result]`.
2. Primary provider: Tavily (changed on 2026-09-27: the owner's `TAVILY_API_KEY` works, with 1,000 free credits each month on the Researcher plan). Brave Search API stays optional. It needs its own API subscription, and we add it only if Tavily is not enough.
3. Use headless Chromium only to open the result pages and the bookmark pages, and to take screenshots. Obey `robots.txt`.
4. Cache search results for 24 hours in the Companion API, so eval runs do not pay twice.

## Consequences

- Monthly cost for our use: 0 USD inside the 1,000 free Tavily credits. We use basic search (1 credit). The 24-hour search cache and trace replay (load tests replay LLM calls, not searches) keep us inside the credits.
- The fact-check rules in the product spec (section 6, rule F2 in ADR-012) do not depend on the provider.
- If we need Google results, SerpAPI is the legal path. We do not need it for 10-03.
