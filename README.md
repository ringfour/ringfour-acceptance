# Ringfour Acceptance

A separate, reusable development control panel for Ringfour. It combines virtual
stack controls with acceptance tests for local changes. Choose
real CLI accounts, organizations and robots in Setup; queue a check; watch suite
results, live checkpoints, cleanup and logs; download the saved evidence.

The application itself needs Python 3.11+ and has no runtime dependencies. It
can be packaged independently. Test implementations stay with the products they
verify: the initial Platform adapter invokes the existing documented
`scripts/apps-acceptance.py` command in a configurable checkout. It does not
import Platform internals or copy the suites into this project.

## Reusable AP campaigns

Use **AP tests** for saved multi-account environments and repeatable build qualification.
It queues 17 regression suites plus live multi-org/multi-robot cases, keeps per-case
logs/build identity, recovers owned-fixture cleanup, and records operator gates
separately. See [the startup and campaign guide](docs/ap-campaigns.md).
The original Runs/Setup single-target checks remain available.

## Start

```bash
cd /home/cdr/ringfour/ringfour-acceptance
python3 -m ringfour_acceptance serve \
  --port 8095 \
  --platform ../ringfour-platform
```

Open **http://127.0.0.1:8095**. This starts the standalone acceptance UI and a
worker under the current Unix account. **No tests or launchers start automatically.**
Starting the control room leaves both stacks in place. Virtual-robot stack
operations run only when an operator clicks a launcher action. Port 8095 is used in
these examples because 8091 and 8093 are already occupied in this workspace.

For an installed package, use `ringfour-acceptance serve --platform /path/to/ringfour-platform`.
Use `--state /path/to/evidence` to choose durable storage (default `.state`
relative to the launch directory), or `--no-worker` for a UI-only process.
The control room binds to loopback; it is not a hosted or LAN service. The browser
recovers its local UI cookie after a service restart; refresh once if an older
page is still reporting repeated 403s. Auth0/worker credentials are separate.

## Setup and two accounts

1. The first worker discovers its authenticated CLI user ID, email, organization
   memberships, organization IDs and robot names/IDs through read-only API calls.
   If no identity appears, prepare `apps/cli` dependencies and log in with the CLI
   in that account's shell. Discovery refreshes every 30 seconds.
2. In **Setup → Connect account**, copy the generated multiline command into
   the second account's shell. A five-minute, single-use code pairs that worker
   with the control room. **Copy command** always generates a fresh code. An expired
   or reused code produces a concise recovery message; request another in Setup.
   Each worker uses its own OS account's CLI/keyring.
   Ringfour access/refresh tokens are never sent to the control room.
3. Add a live target for each account. Select the user, organization and robot.
   The screen shows exact IDs alongside names. The organization must match the
   worker's selected CLI organization; change it in that account's shell with
   `ringfour-cli org select ORGANIZATION_UUID`. Save setup.
4. Enter the registry prefix reachable for publishing and pulling test images.
5. Run **Live preflight** first. **Live** explicitly installs and starts private
   test Apps; the remaining profiles use component or disposable fixtures.

The user-provided targets for this workspace are:

| Account | User ID | Organization ID | Robot ID |
|---|---|---|---|
| cdr / virt1 · Stack 1 | b7278210-3edb-484a-83d4-2191b5cb53ec | 88d119d4-8f51-42dc-b1fd-65376f6b13b3 | 16f8279d-e470-4dba-8d0d-0e3935f93116 |
| ring1 / virty | 87e2593a-a612-4f38-b417-81c7f7c2abbf | 7bb3f783-43da-41a3-b615-baed556b2361 | f0bc5e22-6045-4fb5-971c-165d7d538a86 |

These are reference values, not impersonation credentials or hardcoded defaults.
A worker must discover the corresponding real identity and membership itself.
Connecting ring1 requires running its generated command as ring1; the first
account does not use sudo or read another account's credential store.

## Stack cards

Open **Stacks**, the default view. Existing stacks appear as individual cards with three
component rows: **Runner** (the current Agent service), **Daemon**, and **Sim**.
There is no new fixed Runner container; the application architecture is unchanged.

- **Add stack** creates another saved card with the next free numbered instance.
  It does not start containers. Click **Start** on that card when ready.
- **Start**, **Stop**, **Logs**, and **Delete** act on that exact stack, directly from the card.
  Output stays in the card and is also saved in Runs. Each component also has
  its own Logs link; Runner, Daemon and Sim tabs keep their stdout/stderr separate.
  Only containers owned by that stack are queried, with the latest 120 lines per
  container. A failed Docker log read remains a failed operation with its stderr.
- **Settings** selects the worker and the modified-source build option (enabled by default for development),
  and provides **Update daemon** for a daemon-only update.

Cards show host ports for Runner, the Daemon runtime and device status page.
Unstarted stacks show their assigned ports; the simulator uses the internal
network. Robot UUIDs are read from the Daemon's public device-status page, not
inferred from stack numbers or the selected API account. If host forwarding is
unavailable, the same page is read inside that exact running container. Status
markup is parsed for its dedicated identity field and never logged. Unreachable
robots retain a previously observed UUID as **Last known robot ID**; a confirmed
unenrolled page clears it.

**Delete** checks fresh container inventory. An unused card is removed without
launching containers or invoking the shutdown script. Existing containers use the guarded shutdown path; only a
confirmed success removes the card. Active Apps block deletion. Device identity
and App evidence volumes are retained; stale pre-deletion inventory cannot
resurrect the card. Existing broken/stopped Daemons may require recovery before
the launcher's persisted App-run checks can authorize their shutdown.

Cards persist across page/service reloads, including stacks not started yet.
Numbered instance assignment, count and Compose commands are handled internally.
The first new allocation is instance 2 or higher, avoiding singleton port overlap.
New stacks have independent simulator, Agent and Daemon projects/state. Each
robot still needs separate enrollment before selecting it as a live test target.

The worker reads container state every ten seconds while idle and refreshes it
after an operation. Each card shows when its state was observed. Missing containers
become Not started; failed/offline inventory is Unknown/Unavailable. Partial
stacks, including a simulator left by an interrupted launch, remain visible.
The existing singleton keeps its external Base101 simulator setup and shows Sim
as External. It is not incorrectly associated with an unrelated numbered simulator.

The worker defaults to the sibling `ringfour-virtual` checkout; override with
`serve --virtual /path/to/ringfour-virtual` or `worker --virtual ...`. Reconnecting
workers reassociate cards for the same checkout without duplicating physical
stacks. Docker access belongs to the selected worker's OS account.

Each card has **Rebuild images**. Click it once on any card to prepare the shared
Runner/Agent, Daemon and simulator images for all stacks on that worker's Docker
engine. Watch Output or Runs for progress. Building leaves running containers
alone; **Start** (or **Restart** on a running stack) then applies the prepared
images without building or pulling. Repeat Start/Restart on the other cards.
If an image is missing, rebuild first. Settings → **Restart daemon** applies only
the prepared Daemon image and leaves the simulator/Runner alone. Settings →
**Allow rebuilding modified sources** controls the explicit local source opt-in.

After updating this launcher implementation, restart the standalone development
panel and reconnect account workers using fresh Setup pairing commands. Existing
jobs are not replayed. This does not restart Platform or virtual stacks.

Start/Stop reuse the existing launchers internally. Port-conflict checks,
active-App shutdown refusal, persistent device state and evidence volumes are
preserved. There is no arbitrary Compose argument or volume-deletion control.
Cancellation waits for cleanup and does not undo partial startup; inspect the
component rows afterward. No test profile or page/server load launches a stack.

## Runs and evidence

- **Quick:** contracts, Console types/interactions, CLI, Daemon.
- **Integration:** API and disposable real-protocol suites.
- **Full:** all component, protocol and browser-fixture suites.
- **Live preflight:** read-only identity, robot ownership and readiness.
- **Live:** private Robot Check publish/install/run/settings and lifecycle checks.

All jobs run sequentially across workers, since disposable suites can share
engine/image names. Live profiles queue one job for each saved target; other
profiles use the selected worker. The worker checks the authenticated identity
again immediately before live execution. It never changes CLI organization
selection, enrolls robots or grants roles. Test profiles perform no stack
operations; the separate Virtual robots controls execute operator-requested
launcher operations.

The UI polls progress every two seconds, showing the active suite, elapsed time,
suite outcomes, exit codes, live checkpoint messages and recorded cleanup. Each
suite has expandable output; the combined log streams timestamped test output
as it arrives. Test reporters list individual cases, and quiet suites emit a
ten-second heartbeat. Missing prerequisites stay pending. Cancellation
sends SIGINT to the parent runner, allowing its child cleanup to finish; the UI
shows “stopping” until the worker finishes. Do not interpret cancellation as
confirmed removal: inspect the cleanup results.

Saved runs live in `.state/runs/<uuid>/job.json`. Setup contains only target IDs
and a registry prefix. Worker session credentials are held in memory. On control
room restart unfinished jobs become interrupted; they are never replayed, and
accounts must reconnect. Saved targets rebind when the same discovered user
reconnects and the organization/robot ownership and selected workspace still
validate. Otherwise re-select and save the targets in Setup. If a worker disappears during execution, its job remains active
for review until it returns or the control room restarts.

On Linux, Platform browser checks can use optional user-local Chromium libraries
from `~/.local/share/ringfour-acceptance/browser-libs` (or a directory named by
`RINGFOUR_BROWSER_LIBRARY_PATH` in the worker environment). This avoids relying
on a temporary shell-only `LD_LIBRARY_PATH` export. This workspace's cdr library
cache is prepared there; another account needs its own dependencies or a readable
library path. Missing browser prerequisites remain pending.

The worker preserves complete original evidence in its own writable temporary
folder, shown with each finished job. UI previews retain up to 160 KB per report
or log and 80 KB of runner output; download the run JSON for that captured
snapshot. Browser screenshots and larger original artifacts remain in the worker
evidence directory. Keep that directory when collecting a complete archive.

Passing independent jobs does not establish cross-user sharing, real browser
sessions, invitations or operator-only stack recovery. Original reports retain
`full_live_acceptance: false` and the outstanding human gates.

### Existing live blockers

The earlier cdr live run at `/tmp/rf-live-cdr-20261126b` failed in the released
Robot Check template's summary artifact publication. Its run is terminal, but
uninstall confirmation timed out against virt2's older deployed Daemon schema.
That evidence remains failed. Starting this UI does not resolve either blocker.
The Robot Check source fix is unit-tested but has not been released into the
pinned template. Review these prerequisites before starting another live run.

## Verification

```bash
python3 -m unittest discover -s tests -v
node --check ringfour_acceptance/static/app.js
```

Tests cover target/identity validation, single-use pairing, secret separation,
worker ownership, sequential claims, cancellation and restart behavior. A real
HTTP worker test executes a disposable fake adapter subprocess and verifies that
cancellation waits for its cleanup evidence. These tests do not claim robot E2E.

The browser smoke uses the Platform checkout's prepared Playwright dependency:

```bash
node tests/browser-smoke.mjs
```

It launches its own disposable UI server, connects a fixture worker, selects
user/org/robot IDs, saves setup, queues a preflight, delivers fixture progress,
downloads the report and checks a mobile viewport. It makes no Platform API or
robot calls. In the current WSL environment, Chromium also needs:

```bash
LD_LIBRARY_PATH=/tmp/rf-browser-libs/extracted/usr/lib/x86_64-linux-gnu \
  node tests/browser-smoke.mjs
```

See [the worker protocol](docs/protocol.md) and [validation evidence](docs/validation.md).
