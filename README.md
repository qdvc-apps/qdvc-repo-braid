# qdvc-repo-braid

Merge two (or more) Git repositories into one new monorepo **as if it had
always been a monorepo**. Each repository's history is rewritten so its files
live in a folder you name, and the commits from all repositories are
interleaved by date into a single history.

```
MyApp-mac/  (Swift)        MyApp-gtk4/  (Python)            MyApp/
  m1  Jan 3                  g1  Jan 5                      m1  mac/...
  m2  Jan 9                  g2  Jan 7          ──►         g1  gtk4/...
  m3  Jan 12                                                g2  gtk4/...
                                                            m2  mac/...
                                                            m3  mac/...
```

Every commit keeps its original author, committer, dates, time zones and
message. Because the files have been under `mac/` "since the beginning",
`git log -- mac/Foo.swift` and `git blame mac/Foo.swift` work with no extra
flags, unlike the usual subtree merge.

Part of the [QDVC suite](https://github.com/qdvc-apps). A single Python file,
standard library only.

## Quick start

```sh
./qdvc-repo-braid mac=../MyApp-mac gtk4=../MyApp-gtk4 -o ../MyApp
```

Your source repositories are never modified. The output folder must not exist
yet (or be empty). Sources can be local paths or URLs, and by default the
branch each one has checked out is used.

Preview the interleaved order first, without creating anything:

```sh
./qdvc-repo-braid mac=../MyApp-mac gtk4=../MyApp-gtk4 -o ../MyApp --dry-run
```

### Using it as a Git subcommand (optional)

Git runs any executable named `git-<name>` on your `PATH` as `git <name>`.
To get `git repo-braid`, add a symlink somewhere on your `PATH`:

```sh
ln -s "$PWD/qdvc-repo-braid" ~/.local/bin/git-repo-braid
git repo-braid mac=... gtk4=... -o ...
```

Use `git repo-braid -h` for help; `git repo-braid --help` makes Git look for
a man page instead.

### Requirements

- Python 3.8 or newer (the `python3` from Xcode Command Line Tools is fine).
- Git on `PATH`. Tested with Git 2.43; anything from 2.15 onwards should work.
- Windows: run it as `python qdvc-repo-braid ...`. If paths get long, enable
  `git config --global core.longpaths true`.

## Options

| Option | What it does |
| --- | --- |
| `FOLDER=REPO` (2 or more) | Folder in the monorepo, and the repo whose history goes there. Folders can be nested (`apps/mac=...`). |
| `-o PATH` | Where to create the monorepo. |
| `--branch FOLDER=BRANCH` | Import a branch other than the checked-out one. Repeatable. |
| `--branch-name NAME` | Branch name in the monorepo (default `main`). |
| `--order-by committer\|author` | Which date decides the interleaving (default `committer`, which reflects when a commit actually landed, even after a rebase). |
| `--linear` | Keep only each repo's first-parent history; commits on merged side branches are dropped and merges become ordinary commits. |
| `--subject-prefix '[{folder}] '` | Prefix every commit message, so `git log --oneline` shows which platform a commit belongs to. |
| `--trailer` | Add `Braided-From: FOLDER OLD-COMMIT-ID` to each message. |
| `--tag-prefix '{folder}/'` | How imported tags are renamed (default: `v1.0` → `mac/v1.0`). |
| `--no-tags` | Don't import tags. |
| `--map-file PATH` | Where to write the old → new commit ID table (default `.git/repo-braid-commit-map.tsv`). |
| `--dry-run` | Print the planned order and exit. |
| `--no-checkout`, `--no-gc` | Skip populating the working tree / packing at the end. |
| `--keep-import-refs` | Keep the original histories under `refs/repo-braid-import/` for comparison. |

## How it works

1. A new repository is created and each source's branch and tags are fetched
   into it (objects only; nothing is checked out).
2. Each source's **main line** (its first-parent chain) is merged with the
   others by date. A repo's own order is never changed, even if its dates go
   backwards.
3. For each commit, a new root tree is assembled from the commit's own tree
   placed at its folder, plus the latest tree of every other source that has
   already appeared. No files are copied or checked out, which makes it fast
   (a 4,000-commit test with 200+ merges takes about two seconds).
4. **Merges are preserved.** Commits on a merged side branch keep the other
   folders exactly as they were where the branch started, so they only ever
   change their own folder. Use `--linear` if you'd rather flatten them.
5. Tags are rewritten to point at the new commits, the import refs are
   deleted, the repository is packed, and the result is checked: each folder
   at the tip must be identical to its source's tip, and every source commit
   must have been written.

## What doesn't carry over

The tool warns about each of these when it spots them:

- **Signatures.** GPG/SSH signatures on commits and tags are removed, since
  they would no longer be valid.
- **Submodules.** Git only reads `.gitmodules` at the repository root.
  Combine them into a root `.gitmodules` with prefixed paths.
- **Root-only config.** `.github/` (Actions, issue templates, CODEOWNERS),
  `.gitlab-ci.yml`, `.mailmap` and similar files only work at the root, so
  move or merge them after braiding.
- **Git LFS.** LFS file contents are stored outside Git. Run
  `git lfs fetch --all` in each source and copy `.git/lfs/objects` across.
- **Other branches.** Only one branch per source is imported, and tags that
  point elsewhere are skipped (and counted).
- **Shared history.** If one repo began as a copy of the other, their shared
  commits appear once in each folder.
- Shallow and partial clones are refused, as their history is incomplete.

## After braiding

- Look it over: `git log --graph --oneline`, `git log -- gtk4/`.
- Add root files such as a `README.md`, a combined `.gitignore` or a
  `.mailmap` in a normal commit.
- Use the commit map to update links in issues, or to translate
  `.git-blame-ignore-revs`.
- Push to a new remote: `git remote add origin URL && git push -u origin main --tags`.

## Running the tests

```sh
python3 -m unittest discover -s tests -v
```

The tests build small repositories in temporary folders and ignore your own
Git configuration.

## Cursed knowledge

Platform gotchas found while building this go in
[`cursed-knowledge-reports/`](cursed-knowledge-reports/), following the
[QDVC Cursed Knowledge](https://github.com/qdvc-apps/qdvc-cursed-knowledge)
guidelines.
