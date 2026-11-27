# Reusable AP acceptance campaigns

Open http://127.0.0.1:8095 → **AP tests**. This is the repeatable entry point for a new build; **Runs** retains individual check and launcher history.

## Starting tomorrow

1. Start the development panel if it is not already running:

   ```bash
   cd /home/cdr/ringfour/ringfour-acceptance
   python3 -m ringfour_acceptance serve --port 8095 --platform ../ringfour-platform --virtual ../ringfour-virtual
   ```

2. In **Stacks**, start the independent stacks you intend to test. Platform and virtual stack reload/rebuild operations remain operator actions. Tests never invoke those launchers.
3. In **Setup → Connect account**, copy a fresh pairing command and run it in **ring1's own shell**. The panel already starts a `cdr` account runner. Keep both shells running. Account runners execute the tests using their own CLI credentials; the Runner component on a stack card is the robot's Agent, a separate process.
4. In **AP tests**, select the saved environment, expand **Configure IDs and runners**, and check the build label, API URL and registry prefix. Runner labels survive reconnection; UUIDs remain editable. If a robot is re-enrolled, update its role and stack binding before launching.
5. Choose **Preflight** and **Run campaign**. Missing optional roles remain pending. A and B must both pass before live fixture setup is allowed.
6. Choose **All** for the reusable campaign, or **Live** after a separate regression sweep. Watch each case's status and expand it for timestamped logs and job evidence. Jobs run sequentially, but the A/B live probes overlap to check independent targets.
7. Record operator gate results with a named reviewer and a concrete trace/report reference. Download the Markdown and JSON reports. A fixture browser pass never supplies a real signed-in browser pass.

No tests start just by opening the panel or saving an environment.

## Target roles

| Role | Purpose | Required identity |
|---|---|---|
| A | Private publisher and first robot | Developer/Owner, own authenticated runner |
| B | Private consumer and second robot | Different real user, different organization, different robot/Daemon |
| C | Same immutable App on a second robot in A's organization | Optional Developer/Owner; may use A's runner |
| Operator | Restricted deployment permissions, permitted start/stop | Optional account with an actual Operator membership in A's organization |

Current observed enrollment for the suggested local environment:

| Role | Runner | User UUID | Organization UUID | Robot UUID | Stack |
|---|---|---|---|---|---|
| A | cdr | b7278210-3edb-484a-83d4-2191b5cb53ec | 88d119d4-8f51-42dc-b1fd-65376f6b13b3 | 16f8279d-e470-4dba-8d0d-0e3935f93116 | Stack 1 / virt1 |
| B | ring1 | 87e2593a-a612-4f38-b417-81c7f7c2abbf | 7bb3f783-43da-41a3-b615-baed556b2361 | f0bc5e22-6045-4fb5-971c-165d7d538a86 | Stack 2 / virty |

A's enrollment was observed through the connected account and Stack 1 status. B's user/org were supplied by the operator and its UUID was observed from Stack 2. B's own authenticated runner must verify ownership again. The kraftwerk `virt2` record `91bc543a-db5f-4b90-bb58-5b7abe17a4cd` is a different identity; it is not assumed to be Stack 2. No robot records were deleted.

The current API roles are Operator, Developer and Owner. It has no Viewer role; an Operator is not expected to be denied start/stop.

## Automated case scope

**Regression** includes 18 product suites (including App development fastlane): contracts; Console types/components; CLI; Daemon; API; isolated inventory, engine recovery, backend timings, catalog/shares, publish, install/update/rollback, runs, runtime evidence/decisions, settings, lifecycle; and fixture browser navigation/layout. Protocol suites use disposable services and do not restart the development stacks.

**Live** uses the configured accounts and public APIs/CLI to check:

- Identity, org/robot ownership, mutually private organizations, online/idle readiness, distinct stack bindings and Daemon namespaces; pre-existing installation bindings are recorded.
- Private build/publish/install; foreign IDs; destination-scoped shares; revoked-before-import denial; idempotent imports; installed binding retained after revocation.
- Conditional official listed publishing/discovery/import and ordinary-publisher listing denial.
- Simultaneous A/B runs, second-App slot refusal, A continuity while B stops, new run identity and stale-stop rejection.
- Actual App stdout, immutable artifact bytes/hash after source mutation/deletion, run-scoped Logbook, decisions and competing-answer denial/attribution.
- Fractional live settings reaching the App, invalid values, stale CAS, deliberate handler rejection, and isolation between targets.
- Running update/uninstall refusal; idle update without auto-start; exact rollback; retained evidence after uninstall; reinstall with a fresh installation ID.
- Optional C: A's same immutable release installed on another same-org robot, separate settings and installation bindings. Optional Operator: deployment mutations denied, start/stop permitted.
- Stationary camera capture with two downloaded PNGs and verified hashes; reference Robot Check IMU/settings/summary/lifecycle; authenticated deployment read latency.

The probe never commands robot motion. Private publications use unique campaign/role keys. An official-publisher test needs A to be the API's configured official publisher, not merely an Owner. Leave that environment option off otherwise; the gate stays pending.

The reference Robot Check uses the CLI's pinned release by default. The known pinned summary publication bug remains a real failure until a release fixes it. An explicit **Robot Check preview entrypoint** can qualify a local source fix without modifying the pinned archive; its path/hash are recorded. Do not describe a preview pass as a released-template pass.

## Evidence, failure and cleanup

Each campaign freezes its environment and records catalog version, source commits, dirty tree hashes, acceptance worker code hash, API URL and observed stack container/image IDs. The build label is descriptive: source identity alone cannot prove which source produced an already-deployed image. Keep the configured deployment aligned with the build you intend to qualify.

Failed prerequisites block dependent cases. Missing runners wait for the exact configured label/user; unclaimed cases can follow a matching authenticated reconnection. Changed source blocks new live cases rather than mixing builds. Cleanup remains possible on a new source version.

Accepted fixture IDs are checkpointed before polling. Cancellation waits for the in-flight worker to report before scheduling cleanup. Cleanup stops exact owned run IDs, waits for owned pending installation operations, uninstalls only owned installations and revokes owned shares; baseline bindings must remain unchanged. Immutable publications, imports and terminal evidence stay in the catalog because the public API does not provide deletion for those records. Share grants expire after two hours even if a response is lost before its ID is recorded.

Panel restart marks active jobs/campaigns interrupted and never replays them. Reconnect the relevant account runners, then use **Recover fixture cleanup**. Do not launch another mutating campaign on those targets until cleanup has been reviewed. If the API or robot stays offline, cleanup is failed/pending evidence, not a successful removal claim.

Canonical evidence is saved under `.state/campaigns/<campaign UUID>/campaign.json` and `report.md`; individual job evidence remains under `.state/runs/<job UUID>/job.json`. Original, untruncated worker files are retained in the `/tmp/ringfour-acceptance-*` directory named in each job. The panel does not contain account access/refresh tokens.

## Gates that stay explicit

Real signed-in browser/CLI agreement; keyboard/mobile/dirty navigation on the deployed build; onboarding/credential rotation; invitation/role transitions/last-owner protection; operator-driven live recovery; hosted HTTPS/Auth0 sessions; actual hosted private-registry policy; 10-robot/100-App scaling; identity preservation and retired-route review remain separately attributable operator evidence. AP-16 stays deferred until a second supported platform exists. Two Base101 instances do not close it.

A selected regression or preflight scope can finish without proving full AP acceptance. Reports expose selected-scope automated results and applicable gates separately; `full_ap_acceptance` remains false while the broader deployment/platform matrix is open.

## Observed Stack 1 network repair

The read-only readiness check on this checkout found Stack 1's Agent container running with **no attached Docker network**. Its Daemon reports `agent_unreachable`; App management is online, but camera/IMU cases must stay pending until the Agent is reachable.

The workspace rule in `/home/cdr/ringfour/AGENTS.md` reserves virtual/Platform stack reload/recreate operations for the operator. To recreate only Stack 1's Agent with its configured network/alias, run:

```bash
cd /home/cdr/ringfour/ringfour-virtual
RINGFOUR_ALLOW_DIRTY_APP_SOURCES=true ./upn.sh 1 --start-index 1 --no-build --no-deps --force-recreate agent
```

This is an operator command, not a test step performed by the campaign. Re-run Preflight afterward and inspect current Agent inventory before sensor workflows. The command preserves enrolled Daemon/device volumes.

Optional role setup: editing C or Operator fields automatically checks Include
this role. Uncheck it explicitly to omit that role. Save environment (or Run
campaign, which saves pending edits) to persist the IDs. Accounts and enrolled
robots alone do not populate the campaign role mapping. Existing campaigns keep
their original environment snapshot; rerun Preflight after changing roles.

App development fastlane is an additional reusable isolated protocol suite,
`ap-development`. It builds/installs one development image in a disposable
engine, exercises the actual pinned TLS CLI/Daemon path, runs two source
snapshots with the same image and no second pull, reads durable App output and
verifies session closure stops the App. It uses temporary services and does not
rebuild/restart the operator's Platform or virtual stacks. Reconnect updated
panel/workers to include it in subsequent regression campaigns.
