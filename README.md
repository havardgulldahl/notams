# notams
Monitoring, storing and mapping NOTAMs

[![Fetch NOTAMs](https://github.com/havardgulldahl/notams/actions/workflows/scrape.yml/badge.svg)](https://github.com/havardgulldahl/notams/actions/workflows/scrape.yml)

[![Parse NOTAMS](https://github.com/havardgulldahl/notams/actions/workflows/parse.yml/badge.svg)](https://github.com/havardgulldahl/notams/actions/workflows/parse.yml)


See the map at https://havardgulldahl.github.io/notams/

## Scraper monitoring

The fetch workflow now treats a zero-result scrape as a failure condition.

- Each scrape writes the latest machine-readable run summary to docs/run_history.json.
- A run is marked as zero-result when the source index returns no NOTAM files or
  when parsing finishes with 0 active NOTAMs.
- The scrape workflow fails on zero-result runs so the repository's GitHub Actions
  status reflects the outage.
- The workflow also opens or updates a GitHub issue when a zero-result run occurs.
- If zero-result runs happen 3 days in a row, the latest run summary sets
  escalate: true and the alert issue is updated to reflect the escalation.

The latest human-readable scrape timestamp is still written to docs/scrape_timestamp,
but docs/run_history.json is now the source of truth for alerting and streak
tracking.
