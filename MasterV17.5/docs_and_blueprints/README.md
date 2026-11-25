# Documentation and blueprints

Start here when you need to understand the project or locate an implementation.

| Document | Purpose |
|---|---|
| [Directory guide](DIRECTORY_GUIDE.md) | Canonical paths, ownership boundaries, and where new files belong |
| [Project architecture](PROJECT_ARCHITECTURE.md) | Runtime topology, backend modules, API surface, persistence, and data flow |
| [Tech stack](TECH_STACK.md) | Languages, frameworks, stores, package manifests, and local tooling |
| [Model performance](MODEL_PERFORMANCE.md) | Verified performance evidence, evaluation rules, and where to record metrics |
| [Backend deployment](BACKEND_DEPLOYMENT.md) | Local startup and deployment configuration |
| [Frontend notes](FRONTEND.md) | Vite/React behavior and production server |
| [Product requirements](PRD.md) | Product scope and historical requirements |
| [AI-agent roadmap](AGENT_ROADMAP.md) | Prioritized next steps and handoff protocol |
| [Changelog V17.2](CHANGELOG_V17.2.md) | Audit + repair release: flat stake, 500 ms rule, idempotent orders, walk-forward model gate |
| [Audit report V17.2](AUDIT_REPORT_V17.2.md) | Weakness report of MasterV17.1 with status of every fix |
| [Changelog V17.1](CHANGELOG_V17.1.md) | Brain, event-stream auto trade, market picker |

## Documentation maintenance

- Keep paths relative to the repository root and update this index when adding a
  project-level blueprint.
- Record measured model results only with their market, source, timeframe,
  sample window, and out-of-sample status. Never infer performance from a
  training status or a UI label.
- Keep credentials and local `.env` values out of documentation, commits, and
  generated reports.
- Update the architecture and directory guide whenever source ownership or
  runtime entry points change.
