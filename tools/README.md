# Development tools

- check.py: formatting, lint, types, syntax, schema drift and tests.
- export_schemas.py: generate OpenAPI and journal schema from shared models.
- package_smoke.py: build and install artifacts in a clean environment.
- live_trial.py: explicit real bot acceptance trial through HTTP and MCP.
- delete_trial.py: explicitly authorized live deletion against an existing daemon; sends and deletes only two newly created test messages, retaining durable per-action receipts. No poller or daemon is started; never rerun an uncertain operation or reuse an evidence directory.
- mutation_smoke.py: isolated mutations of access, durable offset and fsync invariants.
- demo.py: real local archive/MCP workflow against a synthetic Bot API; no credentials.
- render_demo.py: pinned terminal recording and documentation GIF/static fallback rendering.

No real token is required by normal tests or CI.
Demo sources and recording details: [docs/demo.md](../docs/demo.md).
