"""Versioned AP case catalog: required evidence, dependencies and execution kind."""

CATALOG_VERSION = 1
REGRESSION = [
    ("contracts", "AP-01", "Contracts and fixtures"),
    ("console-types", "AP-11/21", "Console types"),
    ("console", "AP-11/17/21", "Console component behavior"),
    ("cli", "AP-06/12", "CLI authoring and operations"),
    ("daemon", "AP-03/07–10/17/18", "Daemon durability and lifecycle"),
    ("api", "AP-01/02/05–10/17–19", "API authorization, state and persistence"),
    ("ap03-inventory", "AP-03", "Isolated Agent → Daemon → API inventory"),
    ("ap08-engine", "AP-08", "Nested engine ownership and replacement recovery"),
    ("ap19-backend", "AP-19", "Isolated lifecycle latency and backend timings"),
    ("ap05-catalog", "AP-05", "Isolated catalog, shares and pull grants"),
    ("ap06-publish", "AP-04/06", "Pinned package, build, push and retry"),
    ("ap07-install", "AP-07/08", "Isolated install, configure, update and rollback"),
    ("ap-development", "App development", "LAN source sync, image reuse and session closure"),
    ("ap09-runs", "AP-09", "Isolated run slots, stale stop and replacement"),
    ("ap10-runtime", "AP-10", "Isolated evidence, decisions and recovery"),
    (
        "ap17-settings",
        "AP-17",
        "Isolated fractions, files and acknowledgement recovery",
    ),
    ("ap18-lifecycle", "AP-18", "Isolated official discovery and uninstall/reinstall"),
    ("browser", "AP-11/17/21", "Responsive browser fixtures and navigation"),
]

# Human checks are first-class gates, never inferred from fixture passes.
GATES = [
    (
        "browser-live",
        "AP-11/19/21",
        "Real browser/CLI state agreement",
        "Using both signed-in profiles, follow the exact run IDs through Home, Robots, Apps, Overview, Activity and Logbook; verify start/stop without refresh.",
    ),
    (
        "browser-navigation",
        "AP-11/17/21",
        "Keyboard, mobile, navigation and edit guards",
        "Exercise desktop/tablet/phone, focus return, dirty edits, workspace changes, Back/Forward, direct links and error/retry states on this build.",
    ),
    (
        "onboarding",
        "AP-03/11/21",
        "Pairing and disposable credential lifecycle",
        "Verify expired pairing, new robot enrollment, Access issue/rotate/revoke on a disposable robot, offline/last-seen and stale versus empty inventory.",
    ),
    (
        "roles-invitations",
        "AP-02/13",
        "Invitation and role-transition lifecycle",
        "Use a disposable test membership to verify Operator→Developer→Owner permissions, invitations, owner-only actions and last-owner protection. Do not grant A membership in B before foreign-ID checks.",
    ),
    (
        "live-recovery",
        "AP-07–10/08V/17/18",
        "Operator-controlled two-robot recovery",
        "Record run/container/StartedAt IDs. Replace each Daemon while the other robot continues; verify original-container adoption, offline decisions, API output/artifact recovery, pending settings and removal/reinstall reconciliation. Perform stack operations through operator controls.",
    ),
    (
        "hosted-auth",
        "AP-21",
        "Hosted HTTPS/Auth0 session lifecycle",
        "At the intended HTTPS origin verify real login, callback, refresh, deep link, expiry/recovery and logout with protected reads denied afterward.",
    ),
    (
        "hosted-registry",
        "AP-05",
        "Hosted private-registry pull policy",
        "With the intended provider, verify publish/import/pull and denial without a grant as separate real principals. Local anonymous registry evidence is insufficient.",
    ),
    (
        "performance",
        "AP-19",
        "10-robot / 100-App performance and state trace",
        "Record correlated browser/API/PostgreSQL/WebSocket/Daemon/render timings, percentiles and request scaling. Resolve historical run-state discrepancies against authoritative run IDs.",
    ),
    (
        "retirement",
        "AP-14/15",
        "Identity preservation and retired paths",
        "Confirm existing users, organizations and App evidence remain, and retired Mission/fixed Runner routes/services are absent.",
    ),
]


def make_plan(environment, scope):
    """Return immutable cases; jobs are scheduled separately by the durable store."""
    cases = []

    def add(
        case_id,
        title,
        ap,
        role="A",
        action=None,
        dependencies=(),
        kind="live",
        always=False,
    ):
        cases.append(
            {
                "id": case_id,
                "title": title,
                "ap": ap,
                "role": role,
                "action": action or case_id,
                "dependencies": list(dependencies),
                "kind": kind,
                "always": always,
                "status": "waiting" if kind != "operator" else "requires_operator",
            }
        )

    if scope in {"all", "regression"}:
        for suite, ap, title in REGRESSION:
            add(
                "regression-" + suite,
                title,
                ap,
                role="regression",
                action=suite,
                kind="regression",
            )
    if scope in {"all", "live", "preflight"}:
        for role in ("A", "B", "C", "Operator"):
            add(
                "preflight-" + role,
                f"{role}: real identity, ownership and readiness",
                "AP-02/03/08V/13",
                role,
                "preflight",
            )
    if scope in {"all", "live"}:
        for role in ("A", "B"):
            add(
                "prepare-" + role,
                f"{role}: private disposable App and install",
                "AP-04/06/07/12",
                role,
                "prepare",
                ["preflight-A", "preflight-B"],
            )
            add(
                "foreign-" + role,
                f"{role}: deny foreign robot, App, revision and installation IDs",
                "AP-05/13",
                role,
                "foreign",
                ["prepare-A", "prepare-B"],
            )
        add(
            "share-revoke",
            "A: revoke a private share before import",
            "AP-05",
            "A",
            "share-revoke",
            ["foreign-A", "foreign-B"],
        )
        add(
            "revoked-import",
            "B: revoked grant denies a fresh import",
            "AP-05",
            "B",
            "revoked-import",
            ["share-revoke"],
        )
        add(
            "share-active",
            "A: create destination-scoped private grant",
            "AP-05",
            "A",
            "share-active",
            ["revoked-import"],
        )
        add(
            "import-shared",
            "B: idempotent import and install A's private release",
            "AP-05/07/13",
            "B",
            "import-shared",
            ["share-active", "prepare-B"],
        )
        add(
            "revoke-active",
            "A: revoke after the consumer installation exists",
            "AP-05",
            "A",
            "revoke-active",
            ["import-shared"],
        )
        add(
            "share-retained",
            "B: existing import and binding survive revocation",
            "AP-05/18",
            "B",
            "share-retained",
            ["revoke-active"],
        )
        add(
            "official-publish",
            "A: official listed publication",
            "AP-05/18",
            "A",
            "official-publish",
            ["prepare-A"],
        )
        add(
            "official-discover",
            "B: Store discovery and repeated official import",
            "AP-05/18",
            "B",
            "official-discover",
            ["official-publish", "preflight-B"],
        )
        add(
            "listed-denial",
            "B: ordinary publisher cannot list publicly",
            "AP-05/18",
            "B",
            "listed-denial",
            ["prepare-B"],
        )
        add(
            "start-A",
            "A: start its probe and publish immutable evidence",
            "AP-09/10",
            "A",
            "start",
            ["prepare-A"],
        )
        add(
            "start-B",
            "B: start while A continues",
            "AP-09/13",
            "B",
            "start",
            ["start-A", "prepare-B"],
        )
        add(
            "slot-B",
            "B: a second App cannot take the active robot slot",
            "AP-09",
            "B",
            "slot",
            ["start-B", "import-shared"],
        )
        for role in ("A", "B"):
            add(
                "runtime-" + role,
                f"{role}: verified bytes, Logbook and decision identity",
                "AP-10/12",
                role,
                "runtime",
                ["start-" + role],
            )
            add(
                "foreign-runtime-" + role,
                f"{role}: deny foreign run/file/decision reads",
                "AP-10/13",
                role,
                "foreign-runtime",
                ["runtime-A", "runtime-B"],
            )
        add(
            "settings-A",
            "A: live fractional value, invalid/rejected edits and CAS",
            "AP-17",
            "A",
            "settings",
            ["runtime-A"],
        )
        add(
            "settings-B",
            "B: settings remain isolated from A",
            "AP-17/13",
            "B",
            "settings-isolation",
            ["settings-A", "runtime-B"],
        )
        add(
            "decision-B",
            "B: answer attributed once, competing answer denied",
            "AP-10",
            "B",
            "decision",
            ["runtime-B"],
        )
        add("stop-B", "B: exact stop", "AP-09/12", "B", "stop", ["runtime-B"])
        add(
            "continues-A",
            "A: its run and output continue after B stops",
            "AP-09/13",
            "A",
            "continues",
            ["stop-B", "runtime-A"],
        )
        add("restart-B", "B: fresh run identity", "AP-09", "B", "restart", ["stop-B"])
        add(
            "stale-stop-B",
            "B: old stop cannot terminate the newer run",
            "AP-09",
            "B",
            "stale-stop",
            ["restart-B"],
        )
        add(
            "lifecycle-B",
            "B: running refusals, idle update/rollback, uninstall/reinstall/history",
            "AP-07/12/18",
            "B",
            "lifecycle",
            ["stale-stop-B", "runtime-B"],
        )
        add(
            "stop-A", "A: stop its own exact run", "AP-09", "A", "stop", ["continues-A"]
        )
        add(
            "operator-permissions",
            "Operator: start/stop allowed, deployment mutations denied",
            "AP-02/13",
            "Operator",
            "operator-permissions",
            ["preflight-Operator", "stop-A"],
        )
        add(
            "prepare-C",
            "C: second target in A's workspace",
            "AP-07/13",
            "C",
            "prepare-same-org",
            ["preflight-C", "prepare-A"],
        )
        add(
            "same-org-C",
            "C: independent target bindings/settings in the same organization",
            "AP-13/17",
            "C",
            "same-org",
            ["prepare-C", "prepare-A"],
        )
        for role in ("A", "B"):
            add(
                "camera-" + role,
                f"{role}: stationary camera captures and verified PNG downloads",
                "AP-03/10/12",
                role,
                "camera",
                ["prepare-" + role, "stop-A" if role == "A" else "lifecycle-B"],
            )
            add(
                "robot-check-" + role,
                f"{role}: reference IMU/settings and summary artifact",
                "AP-06/17",
                role,
                "robot-check",
                ["stop-A" if role == "A" else "lifecycle-B"],
            )
        for role in ("A", "B"):
            add(
                "latency-" + role,
                f"{role}: authenticated read latency on this deployment",
                "AP-19",
                role,
                "latency",
                ["preflight-" + role],
            )
        for role in ("Operator", "C", "B", "A"):
            add(
                "cleanup-" + role,
                f"{role}: cleanup only campaign fixtures and compare baseline",
                "AP-07/09/18",
                role,
                "cleanup",
                kind="cleanup",
                always=True,
            )
        for case_id, ap, title, instructions in GATES:
            add("operator-" + case_id, title, ap, kind="operator")
            cases[-1]["instructions"] = instructions
        add(
            "second-platform",
            "Same immutable release on a second supported platform",
            "AP-16",
            kind="operator",
        )
        cases[-1].update(
            status="deferred",
            instructions="Deferred until a second supported target is configured; two Base101 instances do not close AP-16.",
        )
    return cases


def catalog():
    example = make_plan({}, "all")
    return {
        "version": CATALOG_VERSION,
        "cases": [
            {k: v for k, v in item.items() if k not in {"status", "dependencies"}}
            for item in example
        ],
        "scopes": ["all", "regression", "preflight", "live"],
    }
