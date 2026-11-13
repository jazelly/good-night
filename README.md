# good-night

Go to bed while your AI coding agents finish their work. `good-night`
watches **Claude Code** and **Cursor** on your Mac and, once every agent
session has been idle for a few checks in a row, shuts the Mac down safely.

- Reads the agents' own session transcripts, not CPU graphs or screenshots.
- An agent that has finished its turn, or is waiting for your answer, counts
  as **done**. You can answer it in the morning.
- Long-lived shells such as `claude rc` / `claude remote-control` don't count.
  Only real session activity does.
- Uses `sudo -n /sbin/shutdown -h now`, so "Save changes?" dialogs and apps
  that refuse to quit can't block it.
- **Every password and permission prompt happens once, during `setup`.** A
  real `watch` run never shows one: it checks everything up front without
  prompting and refuses to start if something isn't ready.
- Zero dependencies: Python 3.9+ standard library (the `/usr/bin/python3`
  that ships with the Xcode Command Line Tools is fine).

macOS only for now.

## Quick start

```sh
git clone <this repo> good-night && cd good-night

# 1. one-time setup: sudo rule + every macOS permission prompt, then a check.
#    Run it from the terminal app you'll run `watch` from (see below).
./bin/good-night setup        # same as ./scripts/install.sh

# 2. see what it sees
./bin/good-night status

# 3. rehearse: logs everything, never shuts down
DRY_RUN=1 ./bin/good-night watch

# 4. before bed
./bin/good-night watch
```

Optionally put it on your PATH: `ln -s "$PWD/bin/good-night" /usr/local/bin/`.

Run `watch` in a terminal or tmux and leave it alone. When all agents are idle
for `GOOD_NIGHT_IDLE_CHECKS` polls in a row, you get a notification and a
countdown dialog (`GOOD_NIGHT_GRACE` seconds). It re-checks the agents one
last time, then shuts down.

To cancel a countdown, use any of these:

- click **Cancel shutdown** in the dialog,
- press Ctrl-C in the terminal,
- run `good-night cancel` (handy over SSH).

## Commands

| Command | What it does |
|---|---|
| `watch` (default) | Poll, then shut down when everything is idle. `--dry-run` never shuts down; `--once` runs a single check. |
| `status` | List every detector's recent sessions as BUSY / idle / info. Exit code 1 if anything is busy. `--json`, `--all`. |
| `setup` | One-time, interactive: installs the sudoers rule, shows every macOS permission prompt, then runs `doctor`. `--skip-sudo`, `--no-app-grants`. |
| `doctor` | Non-interactive PASS/FAIL check that `watch` won't prompt or fail. Never shows a prompt itself. |
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
| `GOOD_NIGHT_INTERVAL` | `60` | Seconds between checks. |
| `GOOD_NIGHT_IDLE_CHECKS` | `3` | Consecutive all-idle checks needed before the countdown starts (so 3 × 60s = 3 quiet minutes). |
| `GOOD_NIGHT_DETECTORS` | `claude,cursor` | Which detectors to use. |
| `DRY_RUN` / `GOOD_NIGHT_DRY_RUN` | `0` | `1` = log only, never quit apps or shut down. |
| `GOOD_NIGHT_GRACE` | `60` | Countdown seconds (notification + dialog) before shutting down. `0` = no countdown. |
| `GOOD_NIGHT_DIALOG` | `1` | Show the cancellable countdown dialog. |
| `GOOD_NIGHT_NOTIFY` | `1` | Show macOS notifications. |
| `GOOD_NIGHT_QUIET` | `90` | A session with activity in the last N seconds counts as busy even if its turn looks finished. |
| `GOOD_NIGHT_STALE_MINUTES` | `30` | A session that looks mid-turn but has had no activity for this long counts as idle (crashed, killed, or stuck on a permission prompt). |
| `GOOD_NIGHT_LOOKBACK_HOURS` | `12` | Only sessions whose files changed in this window are inspected. |
| `GOOD_NIGHT_QUIT_APPS` | `1` | Ask GUI apps to quit before shutting down. |
| `GOOD_NIGHT_QUIT_WAIT` | `20` | Seconds to wait for apps to quit before sending SIGTERM. |
| `GOOD_NIGHT_KEEP_APPS` | _(empty)_ | Extra bundle ids to leave alone in the quit step (comma-separated). Finder and common terminals are always kept. |
| `GOOD_NIGHT_CAFFEINATE` | `1` | Keep the Mac awake while watching (`caffeinate -is`), so it can't fall asleep before it shuts down. |
| `GOOD_NIGHT_CLAUDE_DIR` | `$CLAUDE_CONFIG_DIR/projects` or `~/.claude/projects` | Claude Code transcripts. |
| `GOOD_NIGHT_CURSOR_DIR` | `~/.cursor/projects` | Cursor transcripts. |
| `GOOD_NIGHT_LOG` | `~/Library/Logs/good-night.log` | Log file (also printed to stderr). |

Example: check every 30s, need 10 quiet checks (5 min), 2-minute countdown:

```sh
GOOD_NIGHT_INTERVAL=30 GOOD_NIGHT_IDLE_CHECKS=10 GOOD_NIGHT_GRACE=120 good-night watch
```

## One-time setup (all prompts happen here)

```sh
./bin/good-night setup
```

It goes through four steps:

1. **sudo rule.** Runs `scripts/setup-sudo.sh`, which asks for your login
   password once in the terminal (details below).
2. **Graceful-quit permission (optional).** For each app open right now,
   macOS shows *"<Terminal> wants access to control <App>"* once. **Allow**
   means that app gets a normal AppleScript `quit` at night, so it can save
   state. **Don't Allow** means it gets SIGTERM instead. Either answer is
   remembered, so nothing asks again. Apps you open later, or that you never
   allowed, just get SIGTERM, which needs no permission. Rerun `setup` any
   time to add apps, or use `--no-app-grants` to skip this step.
3. **Notifications.** Sends one test notification. If macOS asks whether
   *Script Editor* may send notifications (that's how `osascript` notifications
   show up), choose Allow.
4. **Countdown dialog.** Shows a sample dialog. `display dialog` needs no
   permission; this is just so you know what it looks like.

Then it runs `doctor`.

### Run `setup` and `watch` from the same app

macOS grants privacy permissions to the **app that hosts the process**:
Terminal, iTerm2, Ghostty, or Cursor's integrated terminal. Each one has its
own grants. If you set up in iTerm2 and later run `watch` from Terminal, the
grants don't carry over. `doctor` prints which app it's attributing to
(`host app`), and `watch` runs the same checks on startup. Start `watch` in a
normal terminal window, not inside a detached tmux session, because macOS may
attribute those to a different app.

### `doctor`: verify, never prompt

```
$ ./bin/good-night doctor
INFO  host app            permissions are granted to iTerm2 (com.googlecode.iterm2); run `setup` and `watch` from this same app
PASS  sudo shutdown rule  `sudo -n /sbin/shutdown -h now` needs no password
PASS  notifications       osascript notifications already set up (no prompt)
PASS  countdown dialog    osascript `display dialog` needs no permission
PASS  caffeinate          keeps the Mac awake while watching; needs no permission
PASS  claude data         /Users/you/.claude/projects readable
PASS  cursor data         /Users/you/.cursor/projects readable
PASS  quit apps           5 app(s) quit gracefully via AppleScript, 2 via SIGTERM; this step never prompts

=> ready: `watch` will not show any permission prompt
```

| Check | How it's checked without prompting | If it fails |
|---|---|---|
| sudo rule | `sudo -n -l /sbin/shutdown -h now` | `setup`, or `scripts/setup-sudo.sh` |
| notifications | Notification Center prefs (`com.apple.ncprefs.plist`) list Script Editor, or `setup` already ran | `setup`, or `GOOD_NIGHT_NOTIFY=0` |
| quit apps | `AEDeterminePermissionToAutomateTarget(..., askUserIfNeeded=false)`: granted means AppleScript quit, while not asked, denied (-1743) or would-prompt (-1744) means SIGTERM | never fails. Change grants in System Settings > Privacy & Security > Automation > *your terminal* |
| agent data | `listdir` on `~/.claude/projects`, `~/.cursor/projects` | System Settings > Privacy & Security > Files & Folders |
| dialog, caffeinate | need no permission | n/a |

`watch` runs the same checks at startup. If any check is FAIL, it logs why
and exits with code 2 before it starts watching, so nothing can prompt or
fail while you're asleep. In `DRY_RUN`, a missing sudo rule is only a warning.

### The sudoers rule

`shutdown` needs root. `good-night` runs `sudo -n` (non-interactive) and
never asks for or stores your password, so you add a sudoers rule that allows
**exactly** `/sbin/shutdown -h now` without a password, and nothing else.

`setup` does this for you via `scripts/setup-sudo.sh` (`--uninstall` removes
it). The script writes the rule to its own drop-in file,
`/etc/sudoers.d/good-night` (mode 0440, root:wheel), checks it with
`visudo -c` before installing so a typo can't lock you out of sudo, and then
verifies that `sudo -n` works. It never edits `/etc/sudoers`.

**Manual alternative:**

```sh
sudo visudo -f /etc/sudoers.d/good-night
```

Add this single line, with your short user name (`id -un`) in place of `YOUR_USER`:

```
YOUR_USER ALL=(root) NOPASSWD: /sbin/shutdown -h now
```

Then check:

```sh
sudo -k; sudo -n -l /sbin/shutdown -h now && echo OK   # or: good-night check-sudo
```

macOS's default `/etc/sudoers` already contains
`#includedir /private/etc/sudoers.d`, so the drop-in is picked up.

## How shutting down works

A normal "Shut Down" (Apple menu or AppleScript) asks every app for
permission, so one unsaved document or stuck app cancels it. Instead:

1. **Quit apps without prompting.** The list of open apps comes from
   `lsappinfo`, which needs no permission (asking System Events would need
   Automation access). Apps your terminal is already allowed to automate get
   an AppleScript `quit` so they can save state. Every other app gets SIGTERM,
   which needs no permission. Finder, terminal apps, and whatever app is
   running `good-night` are skipped.
2. **Terminate leftovers.** Apps still running after `GOOD_NIGHT_QUIT_WAIT`
   seconds get SIGTERM. An app stuck behind an unsaved-changes sheet will lose
   those unsaved changes.
3. **Shut down.** `sudo -n /sbin/shutdown -h now`. This doesn't ask apps for
   permission, so nothing can block it.

None of these steps can trigger a macOS permission prompt. Set
`GOOD_NIGHT_QUIT_APPS=0` to skip step 1 and go straight to `shutdown`, which
stops apps itself.

## How idle detection works

Each agent has a **detector** that lists recent sessions and labels each one
`busy`, `idle`, or `info` (shown but never blocking). Shutdown happens only
when no session is `busy` for N consecutive checks. If a detector errors, that
counts as busy.

Two rules apply to every detector:

- **Quiet window:** activity within `GOOD_NIGHT_QUIET` seconds means busy.
- **Stale rule:** "mid-turn" with no activity for `GOOD_NIGHT_STALE_MINUTES`
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
  `GOOD_NIGHT_STALE_MINUTES` if your agents do that.
- **Permission prompts:** a session waiting on "allow this command?" looks
  like a running tool until the stale rule kicks in.
- **Transcript formats aren't public APIs**, so they may change. Use `status`
  to sanity-check after upgrading an agent.

## Adding a detector (e.g. Codex)

Detectors live in `good_night/detectors/`. Subclass `Detector`, implement
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
- [ ] `launchd` agent / `good-night arm` for "shut down tonight after ..."
- [ ] Sleep or hibernate instead of shutdown (`pmset sleepnow`)
- [ ] Homebrew formula

## License

MIT
