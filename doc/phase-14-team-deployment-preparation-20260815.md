# Phase 14 Completion Report: Team deployment preparation

- Status: `PARTIAL`
- Date: `2026-08-15`
- Plan phase: `Phase 14`
- Commit/revision: `checkpoint commit created after document validation; unrelated Phase 11–13 working-tree changes are intentionally excluded`

## 1. Objective and scope

Phase 14 records the migration boundary from a personal local application to an internal multi-user service.
It delivers a target topology, threat model, authorization and tenancy contract, infrastructure migration
sequence, operational requirements, and non-deployable Compose/Kubernetes drafts.

The user explicitly authorized this design work while Phase 13 remains `IN_PROGRESS`. Therefore this report
is `PARTIAL`: no shared platform is implemented or declared deployable, and Phase 13's real-model/recovery
acceptance still precedes any production rollout.

## 2. Detailed changes

- `doc/decision-003-team-deployment-preparation.md`: defines the team target topology, immutable tenant
  scope, OIDC/RBAC/edge contract, threat model, secret boundary, Adapter-compatible migration plans,
  capacity/recovery requirements and pre-deployment security Gate.
- `deployment/team/compose.draft.yaml`: documents API/Worker/OpenSandbox service separation, private
  networks and external secrets. Required image variables intentionally have no defaults and the
  `design-only` profile prevents it from being a release configuration.
- `deployment/team/kubernetes.draft.yaml`: documents separate API/Worker service accounts and default-deny
  NetworkPolicy. Deployments have `replicas: 0`; there is no Ingress and image names are placeholders.
- `doc/DEVELOPMENT_PLAN.md`: records Phase 14 as `IN_PROGRESS` and links this partial report. It does not
  change the Phase 13 completion state.

## 3. Interface and invariant changes

No runtime interface or database schema changed. The design preserves Domain IDs/state machines,
AgentRunHandler, FastAPI DTO/OpenAPI and WebUI API contracts. Future infrastructure replacements occur
behind `VirtualWorkspace`/`PublicationJournal`, `SandboxProvider`, `RunHandler` and a newly extracted
database-neutral runtime-store seam.

Required invariants for later implementation are: immutable Project tenant ownership; all reads, writes,
events and object references authorized by tenant scope; queue messages without content/secrets; at-least-once
delivery fenced by `run_id + lease_epoch`; and `AVAILABLE` publication only after content-hash verification.

## 4. Storage and migration impact

No storage changed in this phase. The design specifies an ordered SQLite→PostgreSQL migration with snapshot
reconciliation, a LocalWorkspace→object storage migration with controlled keys and hash manifests, and a
single active Worker→durable queue migration using outbox/fencing. It explicitly rejects dual active claimers
and unaudited rollback that overwrites newer Runs.

## 5. Security and privacy impact

The design rejects every personal-version trust assumption for shared deployment: loopback trust, no login,
single-user filesystem permissions, SQLite locality, inherited environment secrets and a local Docker server.
It requires TLS edge controls, OIDC, RBAC, tenant scope, CSRF/CORS policy, external Secret Manager, scoped
Sandbox credentials, default-deny networks, auditability and negative security tests before shared access.

No secret, real tenant data, public URL, Kubernetes cluster, Compose deployment, database or cloud account was
used or changed during this phase.

## 6. Dependency changes

None. PostgreSQL, object storage, queue, identity provider, reverse proxy and Secret Manager are targets for
future approved Adapter phases, not dependencies added to the current application or lockfiles.

## 7. Verification performed

| Command | Result | Evidence/notes |
|---|---|---|
| `docker compose -f deployment/team/compose.draft.yaml --profile design-only config` with synthetic image variables | `PASS` | Compose syntax resolves without starting services; output keeps all services profile-gated and no port publication. |
| `rg -n "replicas: 0|kind: Ingress|default-deny|REQUIRED_APPROVED" deployment/team` | `PASS` | Kubernetes draft has two zero-replica deployments, no Ingress, default-deny policy and placeholder images. |
| `rg -n "not implemented|not approved|not-deployable|禁止" doc/decision-003-team-deployment-preparation.md deployment/team` | `PASS` | Every artifact marks its design-only status and shared-access prohibition. |
| `git diff --check -- doc/decision-003-team-deployment-preparation.md deployment/team doc/phase-14-team-deployment-preparation-20260815.md` | `PASS` | No whitespace errors in Phase 14 artifacts. |

## 8. Exit Gate evidence

### Team decision document states trust boundaries, facts, migration order, compatibility interfaces and non-reusable local assumptions

Satisfied for design scope by Decision 003 sections 2–7. It identifies PostgreSQL as the runtime/audit fact
target, object storage as content target, and explicitly preserves Domain/Snapshot/Run/Publication facts.

### Core Domain, AgentRunHandler, API DTO and WebUI do not depend on local process management

Satisfied at the documented contract level. `start.ps1`/`stop.ps1` remain local operational adapters; the
future topology communicates through API, RunHandler, SandboxProvider, Workspace and durable-store seams.
Implementation must still extract a database-neutral runtime-store Protocol before PostgreSQL work.

### Documentation design is not described as implemented; auth, multi-tenancy, online database and HA remain future state

Satisfied. All new artifacts use `design-only`, zero replicas or unresolved required image variables and list
the mandatory security Gates. No production endpoint or shared infrastructure was created.

## 9. Architecture deviations and decisions

No current architecture change. Decision 003 is a proposed future migration contract and intentionally does
not amend V1's local-first commitments. The user-authorized overlap with incomplete Phase 13 is recorded as
a sequencing deviation; it prevents a `COMPLETED` claim, not the documentation work itself.

## 10. Known issues and technical debt

- Phase 13's real Agent smoke, active checkpoint recovery and Docker-stop exercise remain incomplete; no
  team deployment should begin before they close.
- Runtime storage currently exposes SQLite-specific implementation types in some assembly paths. A dedicated
  repository/store Protocol plus PostgreSQL Contract Tests is required before migration.
- Tenant schema, role matrix, OIDC provider selection, data residency, retention/RPO/RTO and capacity numbers
  require organization owners and measured workload data; this phase deliberately does not invent them.
- Draft manifests are not security-reviewed deployment manifests and must never be promoted by renaming.

## 11. Next-phase entry check

The Phase 14 design package is ready for architecture/security review, but Phase 14 cannot become
`COMPLETED` until Phase 13 is complete and the listed organization-specific decisions are approved. The next
implementation phase should start with authenticated request principal and tenant-scope contracts, followed by
storage/workspace/queue Adapters and their contract/migration tests—not by exposing the current API publicly.
