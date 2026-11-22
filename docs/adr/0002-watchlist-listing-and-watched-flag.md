# ADR 0002: Watchlist listing and a `watched` flag on objects

* Status: Accepted
* Date: 2026-11-22
* Contract change: adds `GET /watchlist` (with `WatchlistPage`), a required `watched` boolean on
  `ObjectSummary` (and so on `ObjectDetail`), and a `watched` filter on `GET /objects`.

## Context

Version 1 of the contract can create watchlist entries (`POST /watchlist`) and show one object's
entry (`watch` in `GET /objects/{id}`), but the dashboard also has to:

1. show the watchlist itself, as a panel of watched objects;
2. mark watched objects in the objects table with a ★, without one detail request per row;
3. offer a "watched only" preset.

None of these is possible without a request per object.

## Decision

* `GET /watchlist` lists entries, newest first, paginated with the same `limit` / `offset`
  parameters and `Pagination` object as `/objects`.
* `ObjectSummary.watched` (boolean, required, never null: an object either is or is not watched).
* `GET /objects?watched=true|false` filters on it, combining with the other filters (AND).

## Consequences

* The table, panel and preset each need one request.
* `watched` is a new required response field. That is additive for JSON clients that ignore
  unknown fields; a strict client generated from v1 must be regenerated.
* The listing query joins `watchlist`; the join is on its unique `neo_reference_id`, so each
  object still appears once.
