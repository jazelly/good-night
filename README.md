# idle-shutdown

Go to bed while your AI coding agents finish their work. `idle-shutdown`
watches **Claude Code** and **Cursor** on your Mac and, once every agent
session has been idle for a few checks in a row, shuts the Mac down safely.

- Reads the agents' own session transcripts, not CPU graphs or screenshots.
- An agent that has finished its turn, or is waiting for your answer, counts
  as **done**. You can answer it in the morning.
- Long-lived shells such as `claude rc` / `claude remote-control` don't count.
  Only real session activity does.
- Uses `sudo -n /sbin/shutdown -h now`, so "Save changes?" dialogs and apps
  that refuse to quit can't block it.
- Zero dependencies: Python 3.9+ standard library (the `/usr/bin/python3`
  that ships with the Xcode Command Line Tools is fine).

macOS only for now.

## Quick start

```sh
git clone <this repo> idle-shutdown && cd idle-shutdown

# 1. one-time: allow passwordless `shutdown -h now` (asks for your password once)
./scripts/setup-sudo.sh
./bin/idle-shutdown check-sudo

# 2. see what it sees
./bin/idle-shutdown status

# 3. rehearse: logs everything, never shuts down
DRY_RUN=1 ./bin/idle-shutdown watch

# 4. before bed
./bin/idle-shutdown watch
```

Optionally put it on your PATH: `ln -s "$PWD/bin/idle-shutdown" /usr/local/bin/`.

Run `watch` in a terminal or tmux and leave it alone. When all agents are idle
for `IDLE_SHUTDOWN_IDLE_CHECKS` polls in a row, you get a notification and a
countdown dialog (`IDLE_SHUTDOWN_GRACE` seconds). It re-checks the agents one
last time, then shuts down.

To cancel a countdown, use any of these:

- click **Cancel shutdown** in the dialog,
- press Ctrl-C in the terminal,
- run `idle-shutdown cancel` (handy over SSH).

## Commands

| Command | What it does |
|---|---|
| `watch` (default) | Poll, then shut down when everything is idle. `--dry-run` never shuts down; `--once` runs a single check. |
| `status` | List every detector's recent sessions as BUSY / idle / info. Exit code 1 if anything is busy. `--json`, `--all`. |
| `check-sudo` | Verify the sudoers rule (`sudo -n -l /sbin/shutdown -h now`). |
| `cancel` | Cancel a running countdown. |

Example `status`:

```
Claude Code [claude]: 1 busy, 29 idle (sessions touched in last 12h)
  BUSY      2s ago  castscript / Cast Script feature review   tool finished, model is working
  idle      5m ago  castscript / Investigate error            turn finished (end_turn)
  idle     17m ago  open-htz / Map redraw                     turn finished (end_turn)
  ...
Cursor [cursor]: 0 busy, 8 idle (sessions touched in last 12h)
  idle     18m ago  Desktop-github-my-proj-sub2api            turn ended (success)
  idle     85m ago  Desktop-github-my-proj-noop-desktop       stale: prompt sent, agent is working, but no activity for 85m
  info     81m ago  Desktop-github-my-proj-noop-desktop       background terminal still running: pnpm start (not counted)

=> BUSY: shutdown would wait
```

## Configuration

Everything is set with environment variables, and all of them are optional.

| Variable | Default | Meaning |
|---|---|---|
| `IDLE_SHUTDOWN_INTERVAL` | `60` | Seconds between checks. |
| `IDLE_SHUTDOWN_IDLE_CHECKS` | `3` | Consecutive all-idle checks needed before the countdown starts (so 3 × 60s = 3 quiet minutes). |
| `IDLE_SHUTDOWN_DETECTORS` | `claude,cursor` | Which detectors to use. |
| `DRY_RUN` / `IDLE_SHUTDOWN_DRY_RUN` | `0` | `1` = log only, never quit apps or shut down. |
| `IDLE_SHUTDOWN_GRACE` | `60` | Countdown seconds (notification + dialog) before shutting down. `0` = no countdown. |
| `IDLE_SHUTDOWN_DIALOG` | `1` | Show the cancellable countdown dialog. |
| `IDLE_SHUTDOWN_NOTIFY` | `1` | Show macOS notifications. |
| `IDLE_SHUTDOWN_QUIET` | `90` | A session with activity in the last N seconds counts as busy even if its turn looks finished. |
| `IDLE_SHUTDOWN_STALE_MINUTES` | `30` | A session that looks mid-turn but has had no activity for this long counts as idle (crashed, killed, or stuck on a permission prompt). |
| `IDLE_SHUTDOWN_LOOKBACK_HOURS` | `12` | Only sessions whose files changed in this window are inspected. |
| `IDLE_SHUTDOWN_QUIT_APPS` | `1` | Ask GUI apps to quit before shutting down. |
| `IDLE_SHUTDOWN_QUIT_WAIT` | `20` | Seconds to wait for apps to quit before sending SIGTERM. |
| `IDLE_SHUTDOWN_KEEP_APPS` | _(empty)_ | Extra bundle ids to leave alone in the quit step (comma-separated). Finder and common terminals are always kept. |
| `IDLE_SHUTDOWN_CAFFEINATE` | `1` | Keep the Mac awake while watching (`caffeinate -is`), so it can't fall asleep before it shuts down. |
| `IDLE_SHUTDOWN_CLAUDE_DIR` | `$CLAUDE_CONFIG_DIR/projects` or `~/.claude/projects` | Claude Code transcripts. |
| `IDLE_SHUTDOWN_CURSOR_DIR` | `~/.cursor/projects` | Cursor transcripts. |
| `IDLE_SHUTDOWN_LOG` | `~/Library/Logs/idle-shutdown.log` | Log file (also printed to stderr). |

Example: check every 30s, need 10 quiet checks (5 min), 2-minute countdown:

```sh
IDLE_SHUTDOWN_INTERVAL=30 IDLE_SHUTDOWN_IDLE_CHECKS=10 IDLE_SHUTDOWN_GRACE=120 idle-shutdown watch
```

## One-time sudo setup

`shutdown` needs root. `idle-shutdown` runs `sudo -n` (non-interactive) and
never asks for or stores your password, so you add a sudoers rule that allows
**exactly** `/sbin/shutdown -h now` without a password, and nothing else.

**Scripted (recommended):**

```sh
./scripts/setup-sudo.sh          # install
./scripts/setup-sudo.sh --uninstall
```

The script writes the rule to its own drop-in file
`/etc/sudoers.d/idle-shutdown` (mode 0440, root:wheel), checks it with
`visudo -c` before installing so a typo can't lock you out of sudo, and then
verifies that `sudo -n` works. It never edits `/etc/sudoers`.

**Manual:**

```sh
sudo visudo -f /etc/sudoers.d/idle-shutdown
```

Add this single line, with your short user name (`id -un`) in place of `YOUR_USER`:

```
YOUR_USER ALL=(root) NOPASSWD: /sbin/shutdown -h now
```

Then check:

```sh
sudo -k; sudo -n -l /sbin/shutdown -h now && echo OK   # or: idle-shutdown check-sudo
```

macOS's default `/etc/sudoers` already contains
`#includedir /private/etc/sudoers.d`, so the drop-in is picked up.

If the rule is missing, `watch` refuses to start (exit 2) and says why. It
checks again right before shutting down and, if that check fails, shows a
"SHUTDOWN FAILED" notification instead of failing silently. `DRY_RUN=1` works
without the rule.

## How shutting down works

A normal "Shut Down" (Apple menu or AppleScript) asks every app for
permission, so one unsaved document or stuck app cancels it. Instead:

1. **Graceful quit:** every regular (Dock) app is asked to `quit` via
   AppleScript, in parallel, so it can save state. Finder, terminal apps, and
   whatever app is running `idle-shutdown` are skipped.
2. **Terminate leftovers:** apps still running after `IDLE_SHUTDOWN_QUIT_WAIT`
   seconds get SIGTERM. An app stuck behind an unsaved-changes sheet will lose
   those unsaved changes.
3. **Shut down:** `sudo -n /sbin/shutdown -h now`. This doesn't ask apps for
   permission, so nothing can block it.

The first run of step 1 may trigger a macOS prompt asking to let your
terminal control "System Events". Allow it, or set `IDLE_SHUTDOWN_QUIT_APPS=0`
to skip straight to `shutdown`.

## How idle detection works

Each agent has a **detector** that lists recent sessions and labels each one
`busy`, `idle`, or `info` (shown but never blocking). Shutdown happens only
when no session is `busy` for N consecutive checks. If a detector errors, that
counts as busy.

Two rules apply to every detector:

- **Quiet window:** activity within `IDLE_SHUTDOWN_QUIET` seconds means busy.
- **Stale rule:** "mid-turn" with no activity for `IDLE_SHUTDOWN_STALE_MINUTES`
  means idle. This covers crashed or killed sessions and tool-permission
  prompts nobody will answer tonight.

### Claude Code

Each session, whether from the CLI, `claude remote-control`, or the Claude
desktop app's Code tab, appends to
`~/.claude/projects/<project>/<session-id>.jsonl` in real time. The detector
reads the tail of each recent file and finds the last main-chain `user` /
`assistant` record:

| Last record | State |
|---|---|
| assistant, `stop_reason` = `end_turn` (or `stop_sequence`, `max_tokens`, `refusal`) | idle: turn finished |
| assistant `tool_use` = `AskUserQuestion` / `ExitPlanMode` | idle: waiting for your answer |
| assistant with any other `tool_use` | busy: tool running |
| assistant without `stop_reason` | busy: still streaming |
| user `tool_result`, or a new prompt | busy: model is working |
| user `[Request interrupted by user]` | idle |

Bookkeeping records (`custom-title`, `last-prompt`, `cost-state`,
`api-request`, slash-command output, `isMeta`, sidechains) are skipped. **File
mtime is deliberately not trusted**, because Claude rewrites metadata into
long-idle sessions. Activity time comes from record timestamps, plus the mtime
of the session's `subagents/*.jsonl` (Task sub-agents), so a long sub-agent
run keeps its parent busy.

### Cursor

The Cursor IDE agent and the `cursor-agent` CLI write
`~/.cursor/projects/<project>/agent-transcripts/<id>/<id>.jsonl`. Records have
no timestamps, but each completed turn ends with
`{"type":"turn_ended","status":...}`:

| Last record | State |
|---|---|
| `turn_ended` | idle |
| anything else (user prompt, assistant tool calls/text) | busy until stale |

Activity time is the transcript's mtime (or a sub-agent transcript's, if newer).

Agent terminals (`~/.cursor/projects/*/terminals/*.txt`) whose process is
still alive show up as `info` only. Background dev servers (Metro, vite, ...)
"run" forever and would otherwise block shutdown all night. A foreground
command keeps its agent's turn open anyway, so it's already covered.

`~/Library/Application Support/Cursor/User/globalStorage/anysphere.cursor-agent-worker/*.log`
is **ignored**. Those logs get a heartbeat line every 30 s
(`Received frame ctx=worker-mode`) whether or not anything is running.

### Known limitations

- **Background jobs started by an agent after its turn ended** (e.g. Claude
  Code `run_in_background` shells) aren't tracked. Once the turn is over, the
  session is idle.
- **A tool call that runs silently for longer than `STALE_MINUTES`** (a very
  long build with no transcript writes) will eventually count as idle. Raise
  `IDLE_SHUTDOWN_STALE_MINUTES` if your agents do that.
- **Permission prompts:** a session waiting on "allow this command?" looks
  like a running tool until the stale rule kicks in.
- **Transcript formats aren't public APIs**, so they may change. Use `status`
  to sanity-check after upgrading an agent.

## Adding a detector (e.g. Codex)

Detectors live in `idle_shutdown/detectors/`. Subclass `Detector`, implement
`available()` and `sessions()`, return `Session(state=BUSY|IDLE|INFO, ...)`,
pass each one through `self.finalize()` for the shared quiet/stale rules, and
register the class in `detectors/__init__.py`. Add fixture-based tests in
`tests/`.

```sh
python3 -m unittest discover -s tests -v
```

## Roadmap

- [ ] **Codex CLI** detector (`~/.codex/sessions/**/rollout-*.jsonl`)
- [ ] Optional process-tree check for agent-spawned background jobs
- [ ] `launchd` agent / `idle-shutdown arm` for "shut down tonight after ..."
- [ ] Sleep or hibernate instead of shutdown (`pmset sleepnow`)
- [ ] Homebrew formula

## License

MIT
