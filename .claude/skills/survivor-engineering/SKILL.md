---
name: survivor-engineering
description: >-
  Database, performance and concurrency rules for survivor-fantasy: required SQLite
  PRAGMAs, rollback on every commit, avoiding N+1 and missing FK indexes, hoisting
  constants out of loops, snapshots instead of mutating ORM objects, caching
  episode_stats JSON parsing, and the APScheduler rules. Read before changing
  anything that touches the database, a hot loop, or the background refresh job.
---

# survivor-engineering

These were written from code review on this repo. Data integrity and testing rules
stay in CLAUDE.md, as do the data-semantics traps.

## Database (SQLite)

- **Enable PRAGMAs**: SQLite requires `PRAGMA foreign_keys=ON` and `PRAGMA journal_mode=WAL` on every connection. Without these, FK constraints are silently ignored and concurrent reads/writes cause `database is locked` errors. See `create_app()` for the `Engine.connect` event listener.
- **Wrap mutations in try/except + rollback**: Any function that calls `db.session.commit()` must have a corresponding `db.session.rollback()` in an except block. Partial commits leave the DB in an inconsistent state.
- **Batch queries, never N+1**: Never issue a query per item inside a loop. Fetch all related records in one query before the loop and build a lookup dict. Use `joinedload()` or `subqueryload()` for relationships accessed in bulk.
- **Add indexes on FK columns**: SQLite does NOT auto-index foreign key columns. Every FK column used in `filter_by()` or `order_by()` should have an explicit `db.Index()`.
- **Validate foreign key consistency**: When accepting IDs from form inputs, always verify the referenced record belongs to the expected parent (e.g., a survivor_id belongs to the current season_id).
## Performance

- **Don't recompute constants in loops**: If a value doesn't change between iterations (SS streak, elimination day sets, merge episode), compute it once before the loop and pass it in. This is the #1 source of unnecessary CPU work in the codebase.
- **Avoid mutating ORM objects for simulation**: Use lightweight snapshots (`SimpleNamespace`, `dataclass`, or plain dicts) when running Monte Carlo simulations or as-of replays. Mutating live ORM objects risks race conditions with concurrent requests and the background scheduler, and requires fragile save/restore patterns.
- **Always use try/finally for mutate-restore patterns**: If you must mutate and restore ORM objects (e.g., `_apply_as_of`), wrap the usage in `try/finally` so the restore runs even on exceptions.
- **Cache JSON parsing**: `Survivor.episode_stats` is a JSON TEXT column parsed via `json.loads()`. Use a cached property or parse-once pattern — never call `json.loads()` on the same blob multiple times in one request.
- **Prefer dict lookups over DataFrame scans in loops**: When iterating over players/seasons, convert grouped DataFrames to dicts keyed on `(season, castaway_id)` before the loop. A `dict.get()` is O(1); a DataFrame boolean mask is O(n).
## Concurrency

- **Scheduler writes vs web reads**: The APScheduler daily refresh runs in a background thread. WAL mode allows concurrent reads during writes. Any new background job must respect this — never hold a write transaction longer than necessary.
- **Set `misfire_grace_time`** on scheduled jobs: APScheduler's default grace time is 1 second. A server restart at trigger time silently drops the job. Use `misfire_grace_time=3600` and `coalesce=True`.
- **Register `atexit` shutdown** for the scheduler: Without it, Gunicorn SIGTERM can interrupt an in-flight DB write.
