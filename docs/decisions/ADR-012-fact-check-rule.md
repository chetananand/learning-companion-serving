# ADR-012: Fact-check rule (when the live fact wins)

Status: accepted (2026-09-27). The debate is in `DEBATE-LOG.md`, point 5. The full rule text is in `03-product-spec.md`, section 6. Owners: the project owner and Claude.

## Context

The owner asked for a live fact check where the live result wins when it is better. "The live result always wins" is not safe, because a spam page can replace a source that the owner chose. The first rule had a gap. Two of its three paths did not check the page date. Thus an old page was able to correct a newer bookmark.

## Options

| Option | For | Against |
|---|---|---|
| F1: the first rule | Simple. | An old page can win. |
| **F2: the first rule with a date check on every path** | Code decides. A unit test covers each path. A page cannot argue its way to a win. | More date logic. |
| F3: an LLM judge | Flexible. | A unit test cannot fix its result. Text in a fetched page can change its decision. |

## Decision

F2. For `CONTRADICTED` or `OUTDATED`, the live fact wins only if the live page is not older than the bookmark evidence, and one of these is true:

1. The live source tier is the same as the bookmark tier or better.
2. Two or more independent domains give the same correction.

A tier-1 page with no date counts as current. A page with an older date never wins. Else the claim is "disputed", and the answer shows both facts with their dates.

## Consequences

- `app/factcheck/resolve.py` holds the rule, with a unit test for each path.
- Mixed verdicts follow rule 10 of the product spec. A newer `SUPPORTED` page of the same or a better tier blocks a correction.
- The dates come from the page content, not from the HTTP `Last-Modified` header.
- The search provider is Tavily, and Brave is optional (ADR-009).
