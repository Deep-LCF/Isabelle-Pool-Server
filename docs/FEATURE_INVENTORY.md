# Feature inventory: IPS and PIDE MCP (2026-10-21)

The full side-by-side inventory of the two headless PIDE connectors for agents, at IPS commit `6e4dd6f`
and PIDE MCP commit `dd46315` (2026-10-21). It was Appendix B of the technical report (v2) until
2026-10-27; the report's comparison section (Section 7) keeps the design choices, the measurements and
the "only IPS / only PIDE MCP / both" paragraphs, and points here for the table.

The rows were derived from the interaction stack (who owns the document, how checking is triggered and
reported, how state is read, how time is bounded), from each project's stated goals, and from what an
adopter asks before deploying. A concern is a row only if at least one codebase or the preprint documents
a deliberate decision about it.

| Concern | IPS | PIDE MCP |
|---|---|---|
| Sessions per process | pool with leases | named sessions, own logic and options |
| Pooling, reuse, eviction | yes | no |
| Memory and CPU governance | admission gate, caps, Sledgehammer semaphore | statistics only |
| Multi-file project scope | no: one draft node, imports fixed | yes: theories, ML blobs, dependency resolution |
| Agent file editing | no, read-only by design | `edit`, `create_file` |
| Co-presence with a human editor | via disk, file MCP only | via disk, second PIDE process |
| Visible perspective | no | last read or edited range plus context |
| Per-command status | command-granularity ranges | with message positions, timing, `bad` |
| Verification with explicit semantics | `verify_chunk` flags; cold build | inferred from counts |
| Transactional rollback, checkpoints | yes | no |
| Non-invasive queries | guarded transient command, overlays | edit into file, read, remove |
| Sledgehammer | tip and positioned, semaphore; staged `fill_sorries` job (try0, nitpick verdict, isar retry, intro-split, parallel siblings) | opt-in extension, early stop |
| Entity search | hover, definition at a position | `find_entities` over a range |
| Multi-attempt, batch fan-out | yes | scratch theories |
| Cancellation of a running call | edit removal on budget expiry | protocol cancellation |
| Progress notifications | no | yes |
| Bounded waits | everywhere | none on core tools, by design |
| Transport | HTTP; MCP stdio and streamable HTTP | MCP stdio |
| Error model | HTTP status codes; MCP tools return JSON | `isError` results, `structuredContent` |
| Plugin mechanism | no | Isabelle services |
| Shipped agent guidance | tool docstrings, one prompt | three skills |
| Input hardening | ML and IO denylist with policy switch, path roots | none |
| Observability | metrics, probes, console, `/api/v1/memory` | logs, session statistics |
| Automated tests | 560 test functions, about 850 cases | none |
