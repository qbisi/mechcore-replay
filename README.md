# mechcore-replay

The native Mechabellum replays that [mechcore](https://github.com/qbisi/mechcore)
is held to, and the battle documents converted from them. One directory per
game build, added to and never rewritten:

```
replays/<build>/grbr/<name>.grbr     the replay, byte for byte as the game wrote it
replays/<build>/battle/<name>.yaml   `mechcore replay convert` over the replay of the same basename
replays/<build>/SHA256SUMS           the identity of every file in both directories
MECHCORE_REV                         the mechcore commit whose converter wrote `battle/`
```

**A replay is evidence and is never edited.** It is copied from the Steam
installation's replay directory, and only a locally recorded one is admitted:
the downloaded class is a server-side reconstruction whose fields do not agree
with the game's state. `mechcore`'s `docs/rules/` and its `replay/README.md`
say how the two are told apart.

**A battle document is generated and is never hand-corrected.** A value that
looks wrong is a claim about the converter, and belongs in mechcore's
`crates/document/src/convert.rs`. The workflow in `.github/workflows/` checks
out mechcore at `MECHCORE_REV`, builds it, converts every replay, verifies
every document with `mechcore doc verify`, and commits the result. Bumping
`MECHCORE_REV` is how a converter change reaches this corpus, and the diff of
that commit is what the change did to it.

mechcore reads this repository at a commit it names in `replay/REPLAY_REV`,
fetched by `scripts/replay.py sync` into its untracked `work/replay/`. Its
tests and its CI read the corpus there.
