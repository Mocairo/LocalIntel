# Reliability Foundation Design

Date: 2026-05-28
Project: Local Intel / 知微情报中枢

## Purpose

Local Intel is now a GitHub-hosted personal intelligence workbench, not just a local scraping script. The next phase should make daily operation reliable before adding more sources or larger product features.

This design covers the first improvement phase: engineering baseline, configuration hygiene, scheduler visibility, and diagnostic workflow.

## Assumptions

- The primary user is a single local Windows user running the dashboard on `127.0.0.1`.
- The app should remain lightweight and standard-library-first unless a dependency clearly pays for itself.
- Existing behavior should remain compatible with the current PowerShell scripts and `python -m app.*` commands.
- `config.example.toml` is the distributable example config. Local personal config should not be treated as shared project state long term.
- This phase should not redesign the dashboard UI or add new content sources.

## Goals

- Make it clear whether the dashboard and scheduler are running.
- Make it clear when the last successful pipeline run happened and when the next run is expected.
- Provide a single local diagnostic command/API that identifies common setup and network failures.
- Add a minimal automated test baseline for ranking, clustering, configuration, and scheduler timing logic.
- Reduce risk from committed local configuration and missing dependency/test instructions.

## Non-Goals

- No account system, cloud deployment, multi-user permissions, or remote sync.
- No new RSS/GitHub/arXiv/GDELT sources.
- No major frontend redesign.
- No large refactor of `app/web.py` in this phase, beyond small API/UI additions needed for status visibility.

## Proposed Approach

Use a narrow reliability layer over the existing architecture:

1. Add project metadata and test baseline.
2. Introduce structured runtime status derived from existing PID files, report runs, source health, and scheduler timing.
3. Expose that status through a small API and display it in the current dashboard.
4. Improve docs and config hygiene so fresh clones can install, test, run, and configure the project predictably.

This is preferred over starting with UI polish or personalization because reliability failures make all later product improvements harder to trust.

## Architecture

### Project Baseline

Add a lightweight `pyproject.toml` with:

- project name and Python version requirement
- runtime dependencies only if current imports require them
- pytest configuration

Add focused tests under `tests/` for pure logic first:

- `app.scheduler.next_run_at`
- ranking and filtering behavior in `app.ranker`
- clustering behavior in `app.clusters`
- config read/write normalization in `app.config_store`

Network-dependent source fetches remain outside the default test suite.

### Runtime Status

Add a small status module, tentatively `app/status.py`, responsible for computing:

- dashboard PID health
- scheduler PID health
- web port listen state, where available
- last successful report date and timestamp
- latest run errors
- latest source health counts/errors
- next scheduled run time from `daily_time` and timezone

The module should use existing files and database tables. It should not start or stop processes.

### Dashboard/API Integration

Add `GET /api/runtime-status` to `app.web`.

The dashboard should show a compact health panel:

- Dashboard: running/stopped
- Scheduler: running/stopped/untracked
- Last run: date/time plus success/error state
- Next run: local time
- Sources: ok/error summary

The existing source health and progress panels remain. This phase only makes the operational state explicit.

### Diagnostics

Extend `app.doctor` from network-only checks into a local diagnostic command:

- validate Python can import the app
- validate config and env files are readable
- validate data/log/report directories can be created
- validate SQLite can be initialized/opened
- validate configured source endpoints are reachable, preserving current behavior
- print actionable failures with a non-zero exit code when critical checks fail

The dashboard may link to instructions for `python -m app.doctor --env .\.env`; it should not run diagnostics automatically on every page load.

## Data Flow

Runtime status reads from:

- `data/web.pid`
- `data/scheduler.pid`
- `config.toml`
- `report_runs`
- `source_health`
- existing app directory settings

Pipeline execution continues to write `report_runs`, `source_health`, reports, and logs as it does today.

No schema migration is required for this phase unless tests reveal missing data needed for reliable status. If a migration becomes necessary, it must be additive.

## Error Handling

- Missing PID file should be reported as `not_tracked`, not as an exception.
- Stale PID should be reported as `stopped`.
- Missing database should be reported as `not_initialized`.
- Invalid config should surface a clear error in doctor and API status.
- Source failures should be grouped by source and preserve existing error details.
- Dashboard API errors should return JSON with an error field rather than an HTML error body where practical.

## Config Hygiene

Keep `config.example.toml` as the reference file.

For `config.toml`, choose one of these during implementation:

- If it is intended to be user-local, remove it from Git tracking and document copying from `config.example.toml`.
- If it is intended as a checked-in default, keep it free of machine-specific values and ensure local overrides live elsewhere.

The recommended implementation is to stop tracking personal `config.toml` after preserving an example/default path, because user-specific local settings will diverge.

## Testing

Default verification should be:

```powershell
python -m pytest
python -m app.doctor --env .\.env
python -m app.web --config .\config.toml --env .\.env
```

For automated tests, avoid real network calls and avoid depending on the existing local SQLite database.

Expected test coverage for this phase:

- scheduler computes next run correctly before and after the configured daily time
- ranking drops blocked keywords and handles preferred domains
- clustering groups related non-GitHub items and keeps GitHub Trending as standalone
- config updates preserve allowed keys and normalize numeric fields
- runtime status handles missing/stale PID files and missing database

## Implementation Boundaries

Keep changes surgical:

- Add new modules where they reduce coupling.
- Avoid splitting `app/web.py` wholesale in this phase.
- Do not change ranking semantics except where tests document existing behavior.
- Do not change report output format unless required by status visibility.
- Do not modify generated `data/`, `logs/`, or `reports/` artifacts.

## Success Criteria

- A fresh clone has clear install/test/run instructions.
- Running `python -m pytest` gives a useful baseline.
- Running `python -m app.doctor --env .\.env` reports local setup and network health.
- The dashboard shows scheduler and last-run status without requiring PowerShell status scripts.
- The current start/stop/status PowerShell scripts continue to work.
- The change set is small enough to review without mixing unrelated product features.
