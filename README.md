# mechcore-replay

Collected Mechabellum match documents for
[mechcore](https://github.com/qbisi/mechcore), partitioned by game version:

```
matches/<version>/<BattleID>-rN.yaml
```

`<version>` is the game's `Application.version`, as pinned by mechcore's
`GAME_VERSION`. `BattleID` comes from the native battle record and `N` is the
last captured spectator round, which can lag the lobby's displayed round.

A document can hold a complete prefix or start at an available `state(N)`.
It contains only the captured contiguous rounds and their recorded decisions;
missing opening choices and earlier rounds are not invented. One file does
not imply a complete match or a final settlement.

Files are generated from locally recorded native GRBR caches by
`mechcore convert --to match`. The collector leaves the room before converting
and publishes `BattleID-rN.yaml` atomically. Documents are never hand-edited.
See mechcore's [corpus tools](https://github.com/qbisi/mechcore/blob/master/scripts/corpus/README.md)
for collection, conversion and publication commands.

**The repository only grows after the format migration.** New match files are
added without rewriting, renaming or removing existing ones. An existing name
must contain identical bytes. The one-time migration removed the native GRBR
files from the current tree and replaced them with collected match documents;
the earlier native archive remains available in Git history. New GRBR files
are not published here.
