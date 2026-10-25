# mechcore-replay

Collected Mechabellum match documents for
[mechcore](https://github.com/qbisi/mechcore):

```
matches/<version>/<BattleID>-rN.yaml
tournaments/<date>-<region>/<BattleID>.yaml
```

`<version>` is the game's `Application.version`, as pinned by mechcore's
`GAME_VERSION`. `BattleID` comes from the native battle record and `N` is the
last captured spectator round, which can lag the lobby's displayed round.

`matches/` holds spectator captures partitioned by game version. `tournaments/`
holds server-downloaded tournament matches, grouped by the date printed by
`tournament-replays.py list` (`YYYY-MM-DD`) and region (`asia`, `europe` or
`americas`). Dates use the collector's timezone unless explicitly selected
with `--timezone`; use the same timezone when collecting an event again.
Tournament filenames retain the native `BattleID` without a captured-round
suffix. Match headers record the game version in `build`.

A document can hold a complete prefix or start at an available `state(N)`.
It contains only the captured contiguous rounds and their recorded decisions;
missing opening choices and earlier rounds are not invented. One file does
not imply a complete match or a final settlement.

Files are generated from locally recorded or server-downloaded native GRBRs by
`mechcore convert --to match`. The collector leaves the room before converting
and publishes `BattleID-rN.yaml` atomically. Tournament collection uses
`tournament-replays.py download --id <ID> --publish`; existing local collections
can be uploaded with `tournament-replays.py publish --source <directory>`.
Only generated match YAML is uploaded. Native inputs, download manifests and
tournament metadata stay local. Documents are never hand-edited.
See mechcore's [corpus tools](https://github.com/qbisi/mechcore/blob/master/scripts/corpus/README.md)
for collection, conversion and publication commands.

**Ordinary publication is append-only.** New match files are
added without rewriting, renaming or removing existing ones. An existing name
must contain identical bytes. The one-time migration removed the native GRBR
files from the current tree and replaced them with collected match documents;
the earlier native archive remains available in Git history. New GRBR files
are not published here.

The separately authorized, one-time action-normalization refresh regenerated
the 2026-10-24 Saturday tournaments in place: Asia under `2026-10-24-asia`,
Europe under `2026-10-25-europe`, and Americas under `2026-10-25-americas`.
It replaced 734 generated matches and added the previously unconvertible
`20261025--436` match. This exception does not change ordinary publication.

## Tournament verification

The `Tournament matches` workflow runs on additions under `tournaments/` on
master. It compares the push's before and after trees and checks only added
YAML files; modified files and spectator captures are not reverified. It
fetches only the required Git trees, leaving the old native archive's history
unfetched. Empty selections skip the build and verification jobs.

Every run checks out **qbisi/mechcore master**, records its exact commit and
`GAME_VERSION`, and builds `cargo build --release -p mechcore
--no-default-features`. All shards use that one binary. The corpus never
selects a release tag, a PR build, the match's historical simulator version,
or a game backend. Different match versions retain the CLI's warning behavior.

The run checks whole matches using `mechcore verify --backend simulator`.
Complete stdout, stderr, exit codes, JSON reports, input hashes and build
provenance are uploaded as `tournament-reports-*` artifacts, even when a
match fails. Divergences make the run red, but do not cancel the other matches.

Every upload also produces a **Tournament batch report** in the workflow's
Actions Summary and a `tournament-summary` artifact containing `summary.md`
and `summary.json` (90-day retention). It records the selected corpus commit,
exact master simulator commit, game version, batch totals and per-date/region
counts, pass rates, warnings, missing reports, execution errors and associated
match issues. Open Actions → Tournament matches → the upload's run to compare
measurements over time. Manual rechecks produce separate reports; their rates
refer only to the requested selection, not the full archive.

The denominator is every selected match, including missing or failed process
reports. Missing shards or incomplete issue reporting mark the batch report
**INCOMPLETE**. Warning matches and rounds are counted separately because the
verifier can accept a match while warning that some fight comparisons were
skipped. A zero-match selection has no pass rate. The final summary still runs
when a build or verification fails; raw shard artifacts keep the complete
per-match diagnostics.

Crashes, timeouts or malformed process output fail CI without fabricating a
simulator divergence or resolving an existing issue.

Failures create **one issue per BattleID** in qbisi/mechcore with the `match`
label, including pinned source and simulator commits, first failure, failing
rounds, reproduction commands and the full report artifact link. Further
failures update the same issue. Repeated creation after an uncertain network
response first checks the stable match marker. Human comments and notes
outside the generated report block are preserved. A closed issue is reopened
if the match fails again.

**Resolution requires the entire match to verify.** Fixing one round is not a
resolution when later rounds still fail. CI closes an issue only after the
whole command exits 0 and returns a `kind: match`, `valid: true` report.
Explicit warnings follow the CLI's existing contract; their skipped fight
comparisons remain visible in the complete reports.

### Setup and rechecks

Add an Actions repository secret named `MECHCORE_ISSUES_TOKEN` here. Use a
fine-grained token restricted to qbisi/mechcore, with Issues read/write; no
code-writing permission is needed. The normal workflow token only reads this
corpus. Only the issue-reporting step receives the cross-repository token.
The target repository must have a `match` label.

New tournament uploads trigger verification automatically. After a simulator
repair, use Actions → Tournament matches → Run workflow, with **match** set
to the complete `tournaments/YYYY-MM-DD-region/BattleID.yaml` path. It checks
current mechcore master and updates or resolves the existing match issue.

To backfill an earlier upload, set **since** to the full corpus commit SHA
immediately before that upload. The workflow selects only YAML additions
between that commit and the selected corpus revision. Supply either **since**
or **match**, not both. Simulator updates alone never recheck the corpus.

`.github/scripts/test_tournaments.py` tests selection, process-result handling
and issue lifecycle using temporary files and fake verify output; it does not
simulate corpus matches. Automation changes run these checks in pull requests.
