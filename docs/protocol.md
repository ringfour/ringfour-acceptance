# Local control-room protocol · v1

This is a new interface owned by ringfour-acceptance, not a Platform API change.
All routes are loopback HTTP, JSON requests, with an 8 MB request ceiling. Browser
mutations require a same-origin request and HttpOnly/SameSite=Strict cookie.
The server rejects unexpected Host headers. No cross-origin access is enabled.

| Route | Authentication | Request / result |
|---|---|---|
| GET /api/state | Browser cookie | Setup, public workers and saved jobs; no worker tokens |
| POST /api/pair | Browser cookie | Single-use code and safely quoted worker command; 300s expiry |
| POST /api/setup | Browser cookie | Registry prefix and targets; validates against worker identity |
| POST /api/runs | Browser cookie | Profile and worker ID; returns queued job(s) |
| POST /api/cancel | Browser cookie | Job ID; queued → interrupted or running → stopping |
| GET /api/runs/:id/report.json | Browser cookie | Saved job, snapshots and captured logs |
| GET /api/runs/:id/files/:name | Browser cookie | Captured named text file from that job |
| POST /worker/register | Single-use pairing code | Label and discovered identity → worker ID/session token |
| POST /worker/poll | Worker bearer token | Refreshed identity → next claimed job, or null |
| POST /worker/update | Worker bearer token | Owned job ID and progress/result → cancellation flag |

Target: `{worker_id, user_id, org_id, robot_id}`. The identity lists `user`,
`api_url`, `selected_org`, and `organizations: [{id, name, robots: [{id, name}]}]`.
These are discovered with the worker's existing CLI config/keyring, not provided
as arbitrary credentials. The worker independently revalidates live targets.

Profiles are quick, integration, full, preflight and live. Job transitions:
`queued → running → passed|failed|pending|interrupted`; cancellation can transition
`running → stopping → terminal`. On control-room restart all unfinished jobs
become interrupted with a cleanup-review reason. No automatic retries/replay.
A single job is claimed globally at any one time. Only the owning authenticated
worker may update its job. Already terminal jobs reject later updates.

Each state mutation persists atomically as a JSON file. Worker identities,
pairings and session secrets are ephemeral. Saved setup rebinds to new worker sessions only after the same discovered
user and target ownership/selected-workspace checks succeed. Worker logs redact bearer headers and JWTs;
platform credential-store contents and response bodies from discovery failures
are never returned. This local trusted-code tool is not an execution sandbox.

Platform report snapshots can include an active suite with `status: running`
and `started_at`. Terminal suites add `finished_at`, `seconds` and `returncode`.
The browser counts only completed suite results in its progress fraction and
uses the active suite's timestamp to display elapsed time. Individual captured
log files and the combined runner stream remain accessible separately.

## Virtual launcher jobs

New local endpoints: `POST /api/virtual/plan` and `POST /api/virtual/run`, using
the browser cookie/origin controls above. A plan selects a connected worker,
`action` (`build`, `start`, `daemon`, `stop`, `delete`, `status`, `logs`), `topology` (`singleton` or
`numbered`), integer `start_index`/`count` (1–40, range must fit), and boolean
`allow_dirty`. Only numbered `start` supports a count greater than one.

Workers advertise `launchers: {root, available, image_workflow: 1}` at registration/poll, using
an explicitly configured checkout (default sibling ringfour-virtual). A plan
returns a fixed argv/environment, target projects, expected ports, a printable
command and a SHA-256 fingerprint. Run requests include that fingerprint; server
and worker independently regenerate it before queueing/execution. No arbitrary
shell command, environment map, Compose flag, volume-deletion option or repository
path is accepted from the browser. Only a worker advertising launcher support
can claim a launcher job. Jobs use `profile: virtual` and retain a `launcher`
spec/plan; they share the existing globally sequential queue with tests.

Operator clicks submit launcher jobs; test profiles never enqueue them.
Build delegates to `build.sh`, preparing the shared Agent, Daemon, renderer and
simulator images once without creating/restarting containers. Start/daemon/stop
delegate to the checked-out up.sh/upn.sh/down.sh scripts. Start and daemon use
`--no-build --force-recreate`; Compose also uses `--pull never`. Missing images
fail with an instruction to rebuild first. Numbered daemon-only restart skips
the simulator. Build shares the same queue, cancellation, logs and source opt-in
as other launcher jobs. `stack_operations` identifies an attempted launcher
mutation, including image builds; it does not imply containers were restarted.
Status and logs use bounded read-only Docker queries for the exact selected
Compose projects. A launcher report (`virtual-operation.json`) records action,
projects, commands, times, outcome and whether stack operations were attempted.
Stopping retains the launcher's active-App refusal and state/volume retention.
Cancellation interrupts the launcher process group and waits for its cleanup;
it does not claim to roll back partial startup. Interrupted jobs never replay.

The browser can run an action directly; it obtains a fresh plan/fingerprint
before submitting that explicit operator click. Showing the plan first is optional.

## Persistent stack cards

`GET /api/state` additionally returns `stacks`. Workers publish read-only Docker
inventory in launcher metadata every ten seconds. Cards identify exact Compose
projects and show Runner (the existing `agent` service), Daemon and Sim. No
legacy fixed Runner service is created. The legacy singleton's external Base101
simulator is marked external, not claimed as a numbered simulator.

`POST /api/stacks` with `worker_id` adds one saved numbered stack, allocating the
first unused instance from 2–40 for that checkout. Adding a card performs no
Docker mutation. `POST /api/stacks/configure` changes its worker/dirty-source
setting. `POST /api/stacks/action` with `stack_id` and a fixed launcher action
queues an exact single-stack job through the existing worker queue. Cards persist
in `stacks.json`, including stopped/unstarted stacks; successful fresh inventory
clears old running states for absent containers. Inventory failure is shown as
unknown/unavailable rather than stopped. Worker reconnects rebind matching
checkout cards; physical cards are not duplicated per account.

A stale local browser cookie after acceptance-service restart is recovered by
fetching the same-origin home page and retrying the rejected browser API request
once. Requests that fail due to transport loss are not automatically replayed.
Auth0 and worker session credentials are unaffected by this local UI bootstrap.


## Stack ports, device identity and deletion

Cards add `ports: {agent, daemon, status}`, `robot_id` and `identity_state`
(`enrolled`, `not_enrolled`, `unavailable`). Published host mappings take
precedence; unstarted stacks use assigned ports. The worker reads only the
Daemon's public status-page identity detail. If local host forwarding fails,
it reads that same page inside the exact running Docker container; it does not
open the daemon database/credential files or persist the HTML/App output.
Unreachable devices keep a previous UUID as last known; a confirmed unenrolled
page clears it. Missing identity never implies an invented account/robot mapping.

`/api/stacks/action` also accepts `delete`, bound to one card. Fresh inventory
with no selected containers confirms card-only deletion. Otherwise it delegates
to the same guarded shutdown script, preserving state/evidence volumes. A failed
or unconfirmed result retains the card. Only an owning worker's successful
terminal result with a completed/passed virtual-operation report removes it.
Deletion tombstones suppress stale inventory until a newer observation or an
explicit Add of the same numbered slot. Original job evidence remains in Runs.


## Per-container output

A `logs` operation adds each container's Compose `service` and
`logs: {stdout, stderr, returncode}` to its virtual-operation record. stdout and
stderr are generated filenames in that job's captured files, not large inline
JSON bodies. Streams are redacted and stored separately; the worker's normal
bounded previews/download route handles them. The UI maps agent→Runner,
daemon→Daemon and simulator→Sim, preserving tab selection across polling.
Any failing Docker logs call fails the operation while retaining that
container's stderr and the successful captures from its neighbors.

## AP campaigns · new design

Saved environments (`environments.json`) select an isolated-suite runner and
named live roles A (publisher) / B (consumer), with optional C (second robot in
A's organization) and Viewer (read-only member). Each role stores runner label,
expected API user UUID, organization UUID, robot UUID and optional stack UUID.
Registration UUIDs are resolved at execution, so restarting a runner does not
invalidate tomorrow's environment. Robot/organization IDs may be entered
explicitly but must pass live ownership/identity checks; two roles cannot use
one robot or one enrolled Daemon. Workers keep their own credentials.

GET /api/state exposes environments, campaigns and the versioned case catalog.
POST /api/environments saves a validated environment. POST /api/campaigns creates
a frozen environment/build selection and explicit regression/preflight/live
case plan. It starts no stacks. POST /api/campaigns/cancel requests interruption
and then own-fixture cleanup. POST /api/campaigns/evidence records a human gate's
pass/fail with evidence/reference and operator attribution; it cannot change an
automated result. Campaigns persist in campaigns/:id/campaign.json and export
JSON/Markdown reports. Active campaigns never silently replay after server
restart; an explicit cleanup action can recover recorded fixture IDs.

Each runnable case becomes an existing owned worker job (profile compliance),
with campaign/case IDs, public prior-case outputs and its exact expected role.
The worker captures source commits/dirty state and validates /v1/me and target
ownership again. Live cases execute in the selected Platform CLI interpreter,
using a temporary config overlay for the configured organization; they never
change the account's global selection or share tokens. Public fixture IDs are
checkpointed before waiting on operations. Cleanup acts only on campaign-owned
App/installation/run/share IDs and checks baseline installations are preserved.

Cases have explicit dependencies. Failed prerequisites block only dependent
cases; independent cases continue, and cleanup always runs. Conditional/missing
roles and external prerequisites stay pending. Operator checks remain separately
attributed evidence. The AP verdict can pass only when all required selected
cases pass; deferred second-platform gates and human evidence are never
represented as automated coverage. Cancellation does not bypass cleanup.
