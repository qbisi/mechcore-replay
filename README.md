# mechcore-replay

The native Mechabellum replays that [mechcore](https://github.com/qbisi/mechcore)
is held to. One directory per game version, and nothing else:

```
replays/<version>/<name>.grbr     the replay, byte for byte as the game wrote it
```

`<version>` is the game's own version string, `Application.version`, the one a
replay's header carries (`2.0.0.1.2324`). The game matches players and admits
spectators by it, and every client of a match runs the same simulation in
lockstep, so replays of one version are replays of one set of rules. Steam can
ship new files under an unchanged version, and replays from before and after
such an update share a directory.

**A replay is evidence and is never edited.** It is copied from the Steam
installation's replay directory, and only a locally recorded one is admitted:
the downloaded class is a server-side reconstruction whose fields do not agree
with the game's state. mechcore's `replay/README.md` says how the two are
told apart.

**The repository only grows.** A replay is added and never rewritten, renamed
or removed, so mechcore reads it at `master`. Nothing is generated here:
mechcore converts a replay itself when it needs to read one.
