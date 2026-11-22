# ADR 0001: Summary endpoint for dashboard KPIs

* Status: Accepted
* Date: 2026-11-22
* Contract change: adds `GET /summary` and the `Summary`, `AlertCounts`, `ClosestApproach` and `DailyCount` schemas.

## Context

The dashboard's KPI strip shows how many objects are stored and how many are hazardous,
how many objects raised an alert at each severity, the closest recent pass, when the
pipeline last synced, and a 14-day sparkline of approaches per day.

Version 1 of the contract can only answer this by listing every object through
`GET /objects` (at most 100 per page) and counting on the client. That is wrong in
principle, since KPIs would change with page size and drift as data arrives between
pages, and slow in practice. Two figures cannot be derived from `/objects` at all: the
last successful sync (from `ingestion_runs`) and approaches per day (from the history in
`close_approaches`, not only each object's latest pass).

## Decision

Add `GET /summary`, returning server-side aggregates in one response:
`total_objects`, `hazardous_objects`, `watched_objects`, `alert_counts` (objects by the
worst alert on their latest pass), `closest_approach` (nullable), `latest_approach_date`
(nullable), `last_sync_at` (nullable) and `approaches_per_day` (exactly 14 days, zeros
included, oldest first).

Empty or unknown values are `null`, never `0`, following the contract's existing rule.
An empty database gives `closest_approach: null`, not a fake object.

## Consequences

* The UI makes one request for the whole KPI strip; the numbers are consistent with each other.
* A new read endpoint to maintain; it shares the rate limit and error responses with the others.
* Purely additive: existing clients are unaffected.
