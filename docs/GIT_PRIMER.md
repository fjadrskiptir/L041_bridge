# Git primer (for this repo)

## Words you’ll see

| Term | Meaning |
|------|--------|
| **Working tree** | Your actual files on disk right now. |
| **Uncommitted** | Git sees a difference between the **last commit** and your **working tree** (you edited or deleted tracked files). |
| **Staged** | You ran `git add …` (or checked boxes in GitHub Desktop). Those changes are **queued for the next commit** but not saved as a commit yet. |
| **Untracked** | A file Git has **never** recorded. `git status` lists it until you add it—or until **`.gitignore`** tells Git to ignore it. |
| **Commit** | A saved snapshot of the repo (message + exact file tree). Local only until you **push**. |
| **Push** | Upload your new commits to **GitHub** (`origin`). After push, others (and you on another machine) can **pull** them. |
| **Pull** | Download commits from GitHub and merge into your branch. |

## What `.gitignore` does

- It’s a list of **patterns** (paths / wildcards).
- **Untracked** files that match are **hidden** from `git status` and won’t be added by mistake.
- **Already tracked** files are **not** removed from Git by adding them to `.gitignore`—you’d need `git rm --cached <file>` once.

This repo ignores things like `.env`, local SQLite memory DB, and runtime files under `memories/` so they don’t end up on GitHub.

## Neater commits (habits)

1. **One logical change per commit** when you can (e.g. “Ignore local memory files” vs “Delete unused MCP script”).
2. **Message**: imperative, short subject (`Add art webhook tool`), optional body for *why*.
3. **Before commit**: glance at **diff** (Desktop “Changes” tab or `git diff`) so you’re not committing secrets or junk.

### Rewriting old history (squash many commits into one)

Only if **you’re sure no one else relies on your branch history** (or you coordinate). It rewrites commits and needs **`git push --force-with-lease`**. Fine on a personal repo; risky on shared `main`. When in doubt, leave old history alone and just use cleaner commits **from now on**.

## Clone **ComfyUI** outside this repo (GitHub Desktop)

1. **File → Clone repository…** (or **Repository → Clone**).
2. **URL** tab: paste `https://github.com/comfyanonymous/ComfyUI.git`.
3. **Local path** (folder picker): choose the **parent** folder, e.g. `Documents/GitHub`, **not** inside `l041_bridge`.
4. Desktop will create `…/GitHub/ComfyUI` **next to** `l041_bridge`.

**Check:** Full path should look like `…/Documents/GitHub/ComfyUI`, **not** `…/l041_bridge/ComfyUI`.

### If Desktop only shows one repo

**File → Add Local Repository…** only *adds* an existing folder; it doesn’t nest clones. To get a second repo, use **Clone** and set **Local path** explicitly. You can have many repos listed; each points to its **own folder**.

## Terminal equivalent (same result as Desktop)

```bash
cd ~/Documents/GitHub
git clone https://github.com/comfyanonymous/ComfyUI.git
```

Your Loki repo stays in `~/Documents/GitHub/l041_bridge` (or whatever yours is named); ComfyUI sits beside it.
