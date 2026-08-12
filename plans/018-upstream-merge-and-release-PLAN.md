# 018 — Staying Mergeable & Releasing

**Why this plan exists:** `000-CONTEXT.md` D1 says "fork the plugin, don't modify it,
so we can keep pulling `upstream/main` forever." That is a decision, not a procedure.
Upstream (`NousResearch/hermes-agent`) is extremely active — the remote listing shows
dozens of live branches and `main` moved several commits during the survey for this
plan set. Without a written merge discipline, the fork diverges in three weeks and the
decision becomes moot.

**Day-1 touchpoint:** keep the `upstream` remote configured and keep `git diff --stat --
plugins/kanban` empty. That single invariant is what all of this rests on.
**Full build:** 1 h 30 m.

---

## 1. Remotes

```bash
git remote add upstream https://github.com/NousResearch/hermes-agent.git
git remote -v
# origin    https://github.com/harshul-datansh/hermes-agent.git   (ours)
# upstream  https://github.com/NousResearch/hermes-agent.git      (theirs)
```

The sibling `../hermes-agent` checkout already has both remotes; this clone has only
`origin`. Add `upstream` before the first merge, not during it.

---

## 2. The ownership map

The whole strategy is: **our code lives in files upstream does not have.**

| Path | Owner | Merge risk |
|---|---|---|
| `plugins/pmo/**` | **us** | none — upstream has no such path |
| `hermes_cli/pmo_*.py` | **us** | none |
| `tools/pmo_tools.py` | **us** | none |
| `plugins/dashboard_auth/datansh/**` | **us** | none |
| `tests/**/test_pmo_*.py` | **us** | none |
| `plans/`, `design/` | **us** | none |
| `plugins/kanban/**` | upstream | **must stay untouched** |
| `hermes_cli/kanban_db.py`, `projects_db.py` | upstream | read-only for us |
| `hermes_cli/subcommands/__init__.py` | upstream | **one line** to register `pmo` |
| `hermes_cli/dashboard_auth/registry.py` | upstream | **one line** to register the provider |
| `web/src/plugins/registry.ts` | upstream | possibly one line |

Three touch points in upstream files, each one line. That is the entire conflict
surface, and it is worth defending.

### Keep it to three

Where a fourth is tempting, prefer a registration hook over an edit. Hermes already has
a plugin system with lifecycle hooks (`hermes_cli/plugins.py` — `invoke_hook`,
`discover_and_load`); if PM-OS needs to participate in a core flow, register a hook
rather than editing the flow.

Guard it in CI:

```bash
# fails the build if we touched more of upstream than the allowlist
git diff --name-only upstream/main...HEAD \
  | grep -v -E '^(plugins/pmo/|hermes_cli/pmo_|tools/pmo_tools|plugins/dashboard_auth/datansh/|tests/.*test_pmo_|plans/|design/)' \
  | grep -v -F -f .pmo-upstream-touchpoints.txt
```

`.pmo-upstream-touchpoints.txt` lists the three files by name. Any fourth appearing is
a deliberate decision that should require editing that file — which makes it visible in
review instead of accidental.

---

## 3. Merge cadence

**Weekly**, on a fixed day. Not "when we need something."

A weekly merge is 20 minutes. A quarterly merge is two days and usually gets abandoned,
at which point the fork is permanent and every upstream fix has to be hand-ported
forever.

```bash
git fetch upstream
git checkout -b chore/upstream-$(date +%Y%m%d) main
git merge upstream/main
# expected: conflicts only in the three touchpoint files, if at all
python -m pytest tests/hermes_cli/test_pmo_*.py tests/plugins/test_pmo_*.py -q
hermes pmo doctor
```

### The four things to check every merge

1. **`kanban_db.SCHEMA_SQL` changed?** New columns are fine (we only read). A change to
   `_REBUILD_SPECS` matters — that is the `DROP TABLE`-on-drift machinery
   (`000-CONTEXT.md` D2). Read the diff.
2. **`plugins/kanban/dashboard/plugin_api.py` changed?** Those are fixes we may want in
   `plugins/pmo/`. Diff the two files and cherry-pick deliberately. Keep a note of the
   upstream commit `plugins/pmo/` was last synced from — see §4.
3. **`dashboard_auth/base.py` or `middleware.py` changed?** Our provider implements that
   protocol. Re-run `assert_protocol_compliance(DatanshProvider)` — the base module
   ships that helper exactly for this.
4. **Plugin manifest schema changed?** `web/src/plugins/types.ts`. A new required field
   means our manifest needs it too.

---

## 4. Tracking the plugin fork

`plugins/pmo/` is a copy that will drift from `plugins/kanban/`. Make the drift legible:

`plugins/pmo/UPSTREAM.md`:

```markdown
# Fork provenance

Forked from `plugins/kanban/` at upstream commit `222101071` on 2026-08-05.
Last reviewed against upstream: 2026-08-05.

## Intentional divergences
- API mounted at /api/plugins/pmo
- All routes guarded by pmo_authz.require (002)
- Board addressed by explicit slug, never the active board (003)
- `draft` status + finalize gate (009)
- Datansh design system, .pmo-root scoped (007)

## Upstream fixes reviewed and skipped
| Upstream commit | Summary | Why skipped |
|---|---|---|
```

Update `Last reviewed` every merge, even when nothing is ported. A stale date is a
signal; an absent one is a guess.

`git diff upstream/main:plugins/kanban/dashboard/plugin_api.py plugins/pmo/dashboard/plugin_api.py`
is the review command. It will be large after the authz port — read it for *upstream*
changes, not for ours.

---

## 5. Contributing back

Some of this is genuinely upstreamable and worth offering:

| Candidate | Why upstream would want it |
|---|---|
| Multi-user authz for the dashboard | Their own docstring calls it out as missing |
| `draft` status + dispatch gate | Generally useful for any orchestrated board |
| Mention parsing on task comments | Generic kanban feature |
| Per-project config scoping | Their `projects_db` already implies it |

Anything Datansh-specific — the founder's-office hierarchy, the ERP design system, the
client-thread trust model — stays ours.

Upstreaming the authz layer in particular is worth the effort: it is the piece most
likely to conflict on every future merge, and it stops being a merge problem the moment
it lands upstream.

---

## 6. Versioning and release

- `plugins/pmo/dashboard/manifest.json` `version` is the product version. Semver.
- Tag releases on `origin` as `pmo-v0.1.0`.
- `plans/CHANGELOG.md` — one line per release, human-readable, no commit dumps.
- Record the upstream base in the release notes: *"pmo-v0.1.0, on hermes-agent
  `222101071`"*. When something breaks after a merge, the first question is which
  upstream base it was last known good on.

### Branches

```
main                    ← protected, always deployable
feat/pm-os-day1         ← the day-1 build
feat/<plan-number>-*    ← per-plan work after day 1
chore/upstream-YYYYMMDD ← weekly merges
```

Never commit to `main` directly. Never push without asking.

---

## 7. Deployment

Day 1 is `hermes dashboard` on a workstation. First real deployment needs:

- **A service**, not a terminal. `hermes_cli/service_manager.py` and the
  `docker/s6-rc.d/dashboard/` unit exist upstream — use them.
- **TLS** and non-loopback binding (015 §4).
- **`HERMES_HOME` on a real volume**, backed up (014 §6). `pmo.db` is the irreplaceable
  file.
- **Two processes**: dashboard, and the dispatcher/watcher. They restart independently,
  which matters because the watcher is the one that will need restarting.
- **Health endpoint** for the supervisor — `/health`, the one public path (002 §2d).

Container isolation for the shell tool (003 P1) becomes materially more important the
moment this runs anywhere shared. Do not put it on a shared host until that lands.

---

## 8. Tests

```
tests/test_pmo_upstream_boundary.py
  test_no_modifications_to_plugins_kanban
  test_upstream_touchpoints_match_allowlist
  test_pmo_manifest_matches_current_plugin_schema
  test_datansh_provider_satisfies_protocol       # assert_protocol_compliance
```

`test_no_modifications_to_plugins_kanban` is the cheapest high-value test in the suite:
it is a `git diff` in CI, and it protects the entire merge strategy.

## P0 (day-1 touchpoint) / P1

**Day 1:** `upstream` remote added; `plugins/kanban/` untouched (already true — verified
clean); `plugins/pmo/UPSTREAM.md` written at fork time, while you still remember what
you changed.

**P1:** the CI boundary check; the weekly merge routine; `CHANGELOG.md`; service
deployment; upstreaming the authz layer.

## Traps

- Skipping the weekly merge "just this once". Twice becomes never.
- Editing `plugins/kanban/` for a quick fix. It is the only thing keeping merges cheap.
- Forgetting `UPSTREAM.md` at fork time. Reconstructing the divergence list three weeks
  later is guesswork.
- Merging upstream without reading the `kanban_db` schema diff. `_REBUILD_SPECS` drops
  drifted tables.
- Deploying to a shared host before container isolation lands.
