# Validation · 2026-11-26

Environment: Python 3.14.4, Node 24.22.3, Linux/WSL, existing Platform checkout
and its prepared Playwright Chromium. The application requires Python 3.11+;
only the environment above was exercised in this change.

| Check | Command / evidence | Outcome |
|---|---|---|
| Worker/state/HTTP/process regression | `python3 -m unittest discover -s tests -v` | 10 passed |
| Python lint | Platform CLI venv `ruff check ringfour_acceptance tests` | Passed |
| Python format | Platform CLI venv `ruff format --check ringfour_acceptance tests` | 7 files formatted |
| Browser JavaScript syntax | `node --check ringfour_acceptance/static/app.js` | Passed |
| Real browser UI | `LD_LIBRARY_PATH=/tmp/rf-browser-libs/extracted/usr/lib/x86_64-linux-gnu node tests/browser-smoke.mjs` | Passed |
| Independent distribution | `uv build --out-dir /tmp/ringfour-acceptance-dist` | Wheel and source distribution built; UI/fonts included |
| Real current-account discovery | Standalone server at `http://127.0.0.1:8095` | cdr identity, kraftwerk and virt2 discovered read-only; zero jobs started |

The browser check uses an isolated server and fixture worker with synthetic IDs.
It verifies pairing command generation, user/organization/robot selection, setup
saving, preflight job queueing, worker result delivery, progress rendering, logs,
report download and a 390px mobile viewport without horizontal overflow. There
were no browser page errors. Screenshots were visually inspected:

- `/tmp/ringfour-acceptance-setup.png`
- `/tmp/ringfour-acceptance-runs.png`
- `/tmp/ringfour-acceptance-mobile.png`

The process regression executes an actual disposable subprocess behind a real
local HTTP server, then cancels it and verifies its cleanup log before accepting
the interrupted terminal result. It does not call a Platform stack or robot.

No new live acceptance jobs were started during this project change. The previous
live run's failed App result and timed-out uninstall confirmation remain at
`/tmp/rf-live-cdr-20261126b`. No Platform or ringfour-virtual stack was reloaded,
restarted, rebuilt or recreated. A second real-account worker connection and
passing live robot workflows remain unverified; a browser fixture is not evidence
that those external gates passed.

Pairing-error regression: an actual worker CLI process submits an expired code
to a disposable HTTP server. It exits 2 with the server’s explanation and Setup
recovery instruction, without a traceback, exposing the code, or adding a worker.
The UI Copy command action now requests a fresh single-use code before copying.


Verbose-output follow-up: the UI browser smoke also passes with an active suite
and log bytes delivered before its terminal update. The test checks the running
badge, expandable per-suite output and zero completed progress for that active
suite. The Platform runner's seven regressions and actual six-suite
component/API sweep passed; see the Platform automation evidence for commands.

## Virtual launcher controls · 2026-11-26

Implemented direct operator actions in Virtual robots: singleton/numbered Start,
daemon-only update/start, guarded Stop, read-only container status and recent
logs. Optional plan preview shows fixed commands/projects/ports; direct buttons
obtain a fresh plan before queueing. Tests and launchers share one sequential
queue. Plans reject unsupported flags, invalid ranges and stale fingerprints;
startup rejects ports bound by other Compose projects. Stop retains the existing
script's active-App refusal and persistent state. Environment cleanup prevents
an inherited Compose project from redirecting singleton actions.

Validation commands (from ringfour-acceptance):

- `python3 -m unittest discover -s tests -v`: **18 passed**; output saved at
  `/tmp/rf-acceptance-virtual-final-tests.log`. Includes actual temporary launcher
  processes, guarded-stop failure, a real HTTP worker/process cancellation that
  waits for the stub launcher's cleanup, range/port/plan validation, queue
  ownership and same-user saved-target rebinding. The fake Docker executable
  invokes no Docker commands; no real up.sh/down.sh was run in these checks.
- `LD_LIBRARY_PATH=/tmp/rf-browser-libs/extracted/usr/lib/x86_64-linux-gnu node tests/browser-smoke.mjs`:
  **passed**, including numbered multi-robot command preview, explicit Start
  button queueing to a fixture worker, direct status without preview, configured
  singleton port 8084, startup-error rendering and mobile overflow. Fixture jobs
  report synthetic results and never execute real launchers. Screenshots:
  `/tmp/ringfour-acceptance-virtual.png` and
  `/tmp/ringfour-acceptance-virtual-mobile.png`.
- Ruff and JavaScript syntax checks: **passed**.
- `uv build --out-dir /tmp/ringfour-acceptance-virtual-dist`: **passed**; wheel and
  source distribution include the new wrapper modules and static UI.

Only the idle standalone acceptance service was restarted to activate its new
routes/worker capability. Saved run history was retained and the cdr target
rebound after the same API identity and ownership checks. Other account workers
must reconnect using Setup's fresh generated command.

A real **read-only** status job (`9269fb17-76ee-471e-bc32-f620f459a2a5`) passed
through the updated HTTP/worker/CLI path. It inspected exactly the singleton
Compose project and reported its existing Agent/Daemon as running, with the
Daemon ports 8081 and 8084. Its report records `stack_operations: false`. No
Platform or virtual stack was started/rebuilt/recreated/reloaded/stopped by the
agent. Actual operator-triggered lifecycle changes and robot acceptance remain
external checks; a successful status query is not a passing robot workflow.

## Stack cards and session recovery · 2026-11-26

Replaced the action/topology/count form with persistent cards. Each card shows
Runner (existing Agent service), Daemon and Sim, plus exact-stack Start/Stop/Logs,
inline output and a small Settings disclosure. Add stack allocates one next free
numbered instance without launching it. Worker inventory imports existing and
partial stacks, updates component states every ten idle seconds, and clears
missing containers after stop. The legacy singleton is labelled Existing stack
in the UI and its independently managed simulator is External.

- `python3 -m unittest discover -s tests -v`: **23 passed**;
  `/tmp/rf-acceptance-stack-card-tests.log`. New checks cover repeated allocation,
  card persistence/rebinding, exact one-stack actions, unavailable inventory,
  fresh stopped states and exact Compose service discovery, including simulator-only
  partial launches. Previous worker ownership/process/cancellation checks pass.
- The browser smoke passed repeated Add, no launch on Add, one-stack Start/Stop,
  correct stop argv, independent neighbor state, persistence after page reload,
  inline logs, mobile layout and cookie recovery. All mutable lifecycle results
  came from fixture workers; real stacks were not launched/stopped by the test.
- Ruff, format, JS syntax and standalone distribution build passed. Artifacts:
  `/tmp/ringfour-acceptance-cards-dist`; fixture screenshots:
  `/tmp/ringfour-acceptance-stacks.png` and
  `/tmp/ringfour-acceptance-stacks-mobile.png`.

Only the idle standalone acceptance process was restarted to activate stack
routes. The user saw old-backend 404s during editing, temporary connection refusal
at restart and then stale-cookie 403s. The activated `/api/stacks` returned 201;
one actual stopped Stack 3 card was added to complete the user's failed Add
request, without creating a container or queueing a launcher. A real browser check
confirmed the cards and successful state retrieval after intentionally clearing
its local cookie. The UI now bootstraps its same-origin home-page cookie and
retries a rejected API request once. This affects only the local UI session, not
Auth0 credentials or worker pairing. Already-open older JavaScript needs one
page refresh. The real read-only screenshot is
`/tmp/ringfour-acceptance-stacks-live.png`.

No Platform or ringfour-virtual start/stop/rebuild/recreate/reload operation was
performed by the agent. Card creation and inventory are configuration/read-only
operations; real lifecycle actions remain explicit operator clicks.


## Development panel, ports, identity and Delete · 2026-11-26

The UI now presents Ringfour Development and opens on Stacks. Package/repository
identity remains ringfour-acceptance. Builds still use the current local source
checkout; separate branch/tag pins are not implemented by this change.

- Component/worker regression: **28 passed**; command
  `python3 -m unittest discover -s tests -v`, final output
  `/tmp/rf-development-details-tests-final.log`.
- Browser smoke: **passed**, including real UUID/port rendering from fixture
  metadata, delete refusal retaining a card, confirmed deletion removing only
  the selected card, prior Start/Stop/Add behavior and session recovery. All
  lifecycle updates use fixture workers, not actual robot stacks.
- Read-only browser verification against `http://127.0.0.1:8095` confirmed the
  development title/default Stacks view, actual virty UUID
  `f0bc5e22-6045-4fb5-971c-165d7d538a86`, runtime/status ports 8091/8093, and
  Delete control. Screenshot: `/tmp/ringfour-development-stack-details-live.png`.
- Identity checks distinguish page identity from text in App output, validate
  UUIDs, reject unrelated HTML, read only the selected running container during
  fallback, retain last-known identity when unavailable and clear it when
  explicitly unenrolled. Delete checks cover unused stacks without a launcher,
  guarded shutdown refusal, completed-report confirmation, stale-inventory
  suppression and intentional reuse of a deleted numbered slot.

Only the idle acceptance service was restarted. No actual stack was deleted,
started, stopped or rebuilt during this validation. Original run evidence and
saved cards remain. Delete preserves device/App evidence volumes. An existing
broken daemon may need recovery to satisfy the original shutdown guard.

## Per-container logs · 2026-11-26

Each component has a Logs link; captured output uses Runner/Daemon/Sim tabs with
separate stdout/stderr files. Tab selection survives UI polling. Report JSON
contains service/file references instead of duplicating large log bodies. Docker
log-read failures retain the affected container's stderr and fail the job.

- `python3 -m unittest discover -s tests -v`: **30 passed**;
  `/tmp/rf-development-container-logs-tests.log`. Added per-container separation,
  secret redaction and failed-log-read preservation/outcome checks.
- Browser smoke: **passed**, including selecting Daemon directly from its row,
  distinct stdout/stderr, switching to Runner and Sim, and absence of neighboring
  container content in the selected tab. Existing card/lifecycle fixtures pass.
- Real read-only logs job `8e021609-8c49-42ac-8746-491a1f900c3f`: **passed** for
  the numbered Stack 2 Agent, Daemon and simulator, each with separate captured
  streams/exit 0. Its report records `stack_operations: false`.
- Ruff, format, JavaScript syntax and repository whitespace checks passed.

No acceptance restart was needed: existing workers execute the updated wrapper
module for each new logs job. Refresh loads the static tab/link changes. No robot
or Platform stack operation was performed. The separate Adminer 9080 change is
configured and Compose-validated but requires the operator's Adminer-only
recreation under AGENTS.md.

## Dirty-source development default · 2026-11-26

At the user's request, enabled `allow_dirty` on all four existing cards through
the configuration API and changed discovered/new stack defaults to true. Start
and daemon-update plans now include `RINGFOUR_ALLOW_DIRTY_APP_SOURCES=true` for
those cards. The per-card setting remains editable; direct low-level launcher
scripts retain their existing clean-source default unless passed this flag.

All 30 regressions and the browser smoke pass. The smoke verifies a new card is
checked by default and can still toggle the setting off/on before a launch.
Only the idle standalone development panel was restarted to activate the new
creation default. Its live state confirmed all four cards enabled after restart.
No virtual robot or Platform stack was started/rebuilt/reloaded for this change.

## Reusable AP campaigns · 2026-11-27

Implemented the AP tests environment editor, durable dependency scheduler, per-account case jobs, frozen IDs/build fingerprints, conditional/missing prerequisites, operator evidence, cancellation, matching-worker reconnect and explicit post-restart cleanup recovery. Seeded **Local virtual AP** with observed Stack 1/virt1 for cdr and Stack 2/virty plus the operator-provided ring1 user/org IDs. No existing robot registrations were deleted. Added the repeatable operator guide in `docs/ap-campaigns.md`.

Validation performed:

- Platform CLI interpreter: `python -m unittest discover -s tests -v` — **47 passed**. Covers existing worker/stack behavior plus exact-role validation, scheduler dependencies, source drift, cancellation, partial fixture-ID adoption, restart recovery, public HTTP request contracts, real authoring-schema validation, owned cleanup and matching reconnect. These fixtures are control-panel tests, not live AP acceptance.
- Existing `tests/browser-smoke.mjs` — passed against a disposable HTTP panel with fixture workers. New `tests/campaign-browser.mjs` — passed saved environment editing, explicit roles, cross-account job claims, cancel/cleanup, persistent operator evidence input during polling, Markdown/JSON reports and mobile overflow checks.
- Added three previously unexposed product suites to the Platform adapter/catalog. Actual disposable components: **ap03-inventory passed** (`/tmp/rf-development-expanded-suites`); **ap08-engine and ap19-backend passed** (`/tmp/rf-development-expanded-retry`). AP-19 records 20 lifecycle samples with API/PostgreSQL/engine timings, not browser rendering or 10-robot/100-App scale. The first combined adapter process was interrupted with exit 143 during AP-08; its unfinished report was retained, and AP-08/AP-19 were rerun successfully in a separate evidence directory.
- Platform CLI live-adapter safety tests: `uv run --locked pytest -q tests/test_apps_acceptance_live.py` — **6 passed**. Robot Check's child now uses the selected CLI interpreter; an explicit source preview forwards its path without altering the pinned template.
- Ruff checks passed on changed Python modules; both frontend scripts pass Node syntax checks; Python modules compile.
- `uv build --wheel --out-dir /tmp/rf-acceptance-campaign-dist` — passed. Wheel includes the campaign frontend and stationary probe asset.
- Real cdr read-only preflight via `live_cases.py` — passed UUID/organization, idle-state, current manifest/settings/uninstall negotiation and exact Stack 1 binding. Evidence: `/tmp/rf-ap-read-only-preflight-A/case-report.json`. Daemon namespace and immutable container image ID were recorded. This is not a live install/run pass. Inspection also found that Stack 1's Agent is running without an attached Docker network, with inventory `agent_unreachable`; sensor cases now surface this as a pending prerequisite. The repair command is documented for the operator, not performed.

Only the idle standalone development panel was restarted. The live UI exposes 73 catalog cases (17 regression + 56 live/cleanup/operator/deferred cases), the saved environment and all existing stack cards. ring1 must reconnect under its own account before its live role can be qualified. No Platform/virtual stack reload/rebuild/recreate, live App installation or robot motion was performed for this implementation. Full signed-in/live/operator AP acceptance remains open; the reports do not infer it from these tests.

## Shared image builds and fast starts (2026-11-27)

Added Rebuild images per card (shared across all stacks on the engine), separate
from Start/Restart. The fixed build plan calls virtual/build.sh only; start plans
use --no-build --force-recreate and Compose --pull never. Missing images fail
before containers start. Daemon-only restart skips simulator startup entirely.
Source opt-in, fingerprints, per-card output and the sequential queue remain.

- `../ringfour-platform/apps/cli/.venv/bin/python -m unittest discover -s tests -v`:
  **48 passed**, no skips. /tmp/rf-panel-full-tests.log.
- `python3 -m unittest discover -s ringfour-virtual/tests -v` from workspace:
  **17 passed**, using fake Docker/git and a temporary socket. Checks include
  build-only/no-start, three stacks/zero builds, force recreation without pulling,
  missing-image failure, daemon-only restart and preserved shutdown safety.
  /tmp/rf-virtual-tests.log.
- `LD_LIBRARY_PATH=/tmp/ringfour-browser-deps/usr/lib/x86_64-linux-gnu node tests/browser-smoke.mjs`:
  passed disposable HTTP panel/browser with fixture workers, including Rebuild
  images queue/dirty flag, Start plan without build and mobile layout.
  /tmp/rf-panel-browser.log. No real launchers executed by that test.
- Bash syntax, Node syntax and OpenAPI reference checks passed.

Live image rebuilding/recreation remains an operator check under workspace rules.
Restart the standalone development panel and reconnect account workers to activate
the new Python planning code; no queued jobs are replayed. Platform/virtual stacks
were not restarted, recreated or rebuilt during this change.

## Shared image build / start separation (2026-11-27)

Each stack card offers Rebuild images, which queues the fixed `build.sh` launcher
once for the shared Agent/Daemon/renderer/Base101 images on that worker's engine.
No containers are started/restarted by this action. Start/Restart and Restart
daemon use existing local images (`--no-build --pull never`); missing images
fail before container startup. Starts force replacement to apply the newly built
images, while daemon-only restart leaves the simulator and Agent alone. Source
opt-in is retained for builds. Legacy direct shell launches still build unless
`--no-build` is selected. Old workers without `image_workflow: 1` show a reconnect
instruction and have build/start/daemon controls disabled.

Validation:
- Platform CLI interpreter: `../ringfour-platform/apps/cli/.venv/bin/python -m
  unittest discover -s tests -v`: **48 passed**, no skips.
- `python3 -m unittest discover -s ringfour-virtual/tests -v` from the workspace:
  **17 passed**, fake Docker/Git and temporary sockets only. Verifies build-only
  never calls Compose up, three stacks start with zero builds/pulls, missing
  images prevent startup, and daemon-only restart skips the simulator.
- `LD_LIBRARY_PATH=/tmp/ringfour-browser-deps/usr/lib/x86_64-linux-gnu node
  tests/browser-smoke.mjs`: passed against a disposable panel/fixture worker,
  including Rebuild images queueing, reviewed no-build starts, old-worker gating,
  existing card controls, container logs and mobile overflow.
- Shell syntax and frontend Node syntax checks passed.

Logs: /tmp/rf-panel-full-tests.log, /tmp/rf-virtual-tests.log,
/tmp/rf-panel-browser.log. These checks do not constitute a live image build or
live stack replacement. No Platform/virtual stack was rebuilt/restarted. Existing
live panel/workers have been left connected; restart the standalone panel and
re-pair account workers to load the Python changes before using these controls.

## Optional role setup and real preflight (2026-11-27)

The optional C/Operator editor previously discarded populated fields whenever
Include this role remained unchecked. Editing a role field or choosing its
account now selects Include this role automatically. Explicitly unchecking it
still excludes it on save. The saved-status tooltip lists included roles.
The campaign browser fixture verifies optional account selection, persistence of
all four roles, C's distinct UUID and explicit opt-out.

Reconciled Local virtual AP using the actual connected account/stack identities:
C is cdr's virt3 639e804d-96b8-4f81-9c5c-2fe911493702 on Stack 3 in kraftwerk;
Operator is ring2 cfb93c95-283a-464b-9009-d3939e3f275b in kraftwerk, using A's
robot for permission tests. A/B remain unchanged. Submitted a new read-only
preflight through the authenticated panel API. Campaign
72a054c3-d038-48c9-b4b5-79330ae22b62 passed A, B, C and Operator. Real worker
evidence: /tmp/ringfour-acceptance-222zbrxr, /tmp/ringfour-acceptance-k4pitczn,
/tmp/ringfour-acceptance-3qlxe1jp, /tmp/ringfour-acceptance-413gcx84. No App was
installed/run and no stack operation was performed. Historical campaigns retain
their original snapshots and missing-role results; this does not establish a full
AP compliance pass. Frontend changes load on browser refresh without a restart.

## Optional roles and live preflight (2026-11-27)

The saved Local virtual AP environment still contained only A/B despite the
accounts and robots being enrolled. Saved C as cdr's new kraftwerk virt3 UUID
639e804d-96b8-4f81-9c5c-2fe911493702 on Stack 3, and Operator as ring2 on
kraftwerk. Read-only campaign 72a054c3-d038-48c9-b4b5-79330ae22b62 passed all
four role preflights. No live App/stack mutations were performed.

Optional role fields now stay visible and editing one automatically includes it;
explicit unchecking still omits that role when saved without subsequent edits.
`LD_LIBRARY_PATH=/tmp/ringfour-browser-deps/usr/lib/x86_64-linux-gnu node
tests/campaign-browser.mjs` passed the disposable HTTP/browser flow, including
persistence of all four roles, explicit opt-out, existing campaign cancellation,
cleanup, operator evidence and mobile layout. Log:
/tmp/rf-optional-roles-browser.log. Both changed JavaScript files pass Node syntax
checks. Reload the browser for static updates; no service restart is required.

## App development suite (2026-11-27)

Added ap-development to the saved campaign catalog and Platform adapter. Actual
isolated process evidence is /tmp/rf-fastlane-e2e-release-check.log: real pinned
TLS source upload, two normal App runs with distinct source provenance, same
image/no second pull, workspace ownership/isolation, durable output and closure.
The panel's 48 tests, card browser/mobile checks and 17 stub launcher checks pass.
Live daemon image build/recreation was subsequently explicitly authorized by the
user; three daemons now advertise/publish TLS ports 8443/8453/8463. No Agent or
simulator was restarted. Existing account-worker connections were preserved;
restart/reconnect when loading the expanded Python campaign catalog.
