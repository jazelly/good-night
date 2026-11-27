# good-night

Go to bed while your AI coding agents finish their work. `good-night`
watches **Claude Code**, **Cursor**, and **Codex** and, once every agent session has been
idle for a few checks in a row, puts the computer to **sleep** (macOS
default), **hibernates** it (Windows default), turns the display off, or
shuts it down. You choose with `GOOD_NIGHT_ACTION`.

- Reads the agents' own session transcripts, not CPU graphs or screenshots.
- An agent that has finished its turn, or is waiting for your answer, counts
  as **done**. You can answer it in the morning.
- Long-lived shells such as `claude rc` / `claude remote-control` don't count.
  Only real session activity does.
- **Every password and permission prompt happens once, during `setup`.** A
  real `watch` run never shows one: it checks everything up front without
  prompting and refuses to start if something isn't ready. The default
  `sleep` on macOS needs no sudo at all; only `shutdown` does.
- Zero dependencies: Python 3.9+ standard library (the `/usr/bin/python3`
  that ships with the Xcode Command Line Tools is fine).

Runs on **macOS** and **Windows**. Linux has minimal support (`systemctl`).

## Actions

| `GOOD_NIGHT_ACTION` | macOS | Windows | Linux (minimal) |
|---|---|---|---|
| `sleep` | **default.** `pmset sleepnow` (no sudo, apps stay open) | `SetSuspendState(Hibernate=FALSE)` via powrprof.dll | `systemctl suspend` |
| `hibernate` | rejected with an explanation (see below) | **default.** `shutdown /h` (needs hibernation enabled) | `systemctl hibernate` |
| `display-off` | `pmset displaysleepnow` | `SC_MONITORPOWER` broadcast (user32) | not supported |
| `shutdown` | quit apps, then `sudo -n /sbin/shutdown -h now` (needs the sudoers rule) | close app windows, then `shutdown /s /t 0 /f` | `systemctl poweroff` |

Aliases: `suspend` = `sleep`, `poweroff` = `shutdown`, `screen-off` /
`monitor-off` = `display-off`. `good-night actions` prints this table for
your machine. An unknown or unsupported action is an error at startup; it
never falls back to something else.

```sh
GOOD_NIGHT_ACTION=sleep good-night watch          # macOS default anyway
GOOD_NIGHT_ACTION=shutdown good-night watch       # needs `setup --action shutdown` once on macOS
good-night watch --action display-off             # flag beats the env var
```

Notes:

- **`hibernate` on macOS** is not offered. macOS has no "hibernate now"
  command: `pmset sleepnow` sleeps, and whether memory is also written to
  disk depends on `hibernatemode` (3 = safe sleep, the laptop default; 25 =
  real hibernate, changed with `sudo pmset -a hibernatemode 25`, which
  affects every sleep). good-night won't change that system setting for you,
  so it refuses `hibernate` with that explanation instead of silently doing
  something else.
- **`sleep` / `hibernate` / `display-off` keep your apps open.** Only
  `shutdown` closes apps first.
- After `sleep`, `hibernate` and `shutdown`, `watch` exits; start it again
  the next night. After `display-off` it exits too (the computer keeps
  running and may then sleep on its own per your energy settings).
- The keep-awake helper (`caffeinate` on macOS, `SetThreadExecutionState`
  on Windows) is stopped right before the action, so it can't block sleep.

## Quick start

```sh
git clone <this repo> good-night && cd good-night

# 1. one-time setup: every prompt the chosen action needs, then a check.
#    Run it from the terminal app you'll run `watch` from (see below).
./bin/good-night setup                    # default action (sleep): no sudo needed
./bin/good-night setup --action shutdown  # also installs the sudoers rule

# 2. see what it sees
./bin/good-night status

# 3. rehearse: logs everything, never sleeps / shuts down
DRY_RUN=1 ./bin/good-night watch

# 4. before bed
./bin/good-night watch
```

Optionally put it on your PATH: `ln -s "$PWD/bin/good-night" /usr/local/bin/`.

Run `watch` in a terminal or tmux and leave it alone. When all agents are idle
for `GOOD_NIGHT_IDLE_CHECKS` polls in a row, you get a notification and a
countdown dialog (`GOOD_NIGHT_GRACE` seconds). It re-checks the agents one
last time, then runs the action.

To cancel a countdown, use any of these:

- click **Cancel** in the dialog (or **Sleep now** / **Shut down now** to go immediately),
- press Ctrl-C in the terminal,
- run `good-night cancel` (handy over SSH).

## Commands

| Command | What it does |
|---|---|
| `watch` (default) | Poll, then run the action when everything is idle. `--action`, `--dry-run` (never acts), `--once` (single check). |
| `status` | List every detector's recent sessions as BUSY / idle / info. Exit code 1 if anything is busy. `--json`, `--all`. |
| `setup` | One-time, interactive: whatever the action needs (macOS `shutdown`: the sudoers rule and graceful-quit prompts; always: notification + sample dialog), then `doctor`. `--action`, `--skip-sudo`, `--no-app-grants`. |
| `doctor` | Non-interactive PASS/FAIL check that `watch` won't prompt or fail with this action. Never shows a prompt itself. `--action`. |
| `actions` | Print the action matrix and which one is the default here. |
| `check-sudo` | macOS: verify the sudoers rule (`sudo -n -l /sbin/shutdown -h now`). |
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
Codex [codex]: 0 busy, 3 idle (sessions touched in last 12h)
  idle     12m ago  my-project / Bug fix                      turn complete

=> BUSY: sleep would wait
```

## Configuration

Everything is set with environment variables, and all of them are optional.

| Variable | Default | Meaning |
|---|---|---|
| `GOOD_NIGHT_ACTION` | `sleep` (macOS, Linux), `hibernate` (Windows) | What to do once everything is idle: `sleep`, `hibernate`, `display-off`, `shutdown`. See [Actions](#actions). `watch --action` overrides it. |
| `GOOD_NIGHT_INTERVAL` | `60` | Seconds between checks. |
| `GOOD_NIGHT_IDLE_CHECKS` | `3` | Consecutive all-idle checks needed before the countdown starts (so 3 × 60s = 3 quiet minutes). |
| `GOOD_NIGHT_DETECTORS` | `claude,cursor,codex` | Which detectors to use. |
| `DRY_RUN` / `GOOD_NIGHT_DRY_RUN` | `0` | `1` = log only, never quit apps or run the action. |
| `GOOD_NIGHT_GRACE` | `60` | Countdown seconds (notification + dialog) before the action. `0` = no countdown. |
| `GOOD_NIGHT_DIALOG` | `1` | Show the cancellable countdown dialog. |
| `GOOD_NIGHT_NOTIFY` | `1` | Show macOS notifications (Windows/Linux: a console bell). |
| `GOOD_NIGHT_QUIET` | `90` | A session with activity in the last N seconds counts as busy even if its turn looks finished. |
| `GOOD_NIGHT_STALE_MINUTES` | `30` | A session that looks mid-turn but has had no activity for this long counts as idle (crashed, killed, or stuck on a permission prompt). |
| `GOOD_NIGHT_LOOKBACK_HOURS` | `12` | Only sessions whose files changed in this window are inspected. |
| `GOOD_NIGHT_QUIT_APPS` | `1` | `shutdown` only: ask GUI apps to quit first. |
| `GOOD_NIGHT_QUIT_WAIT` | `20` | Seconds to wait for apps to quit (then SIGTERM on macOS, `shutdown /f` on Windows). |
| `GOOD_NIGHT_KEEP_APPS` | _(empty)_ | Extra apps to leave alone in the quit step (comma-separated): bundle ids on macOS, process names on Windows (`notepad`). Finder/Explorer and common terminals are always kept. |
| `GOOD_NIGHT_CAFFEINATE` | `1` | Keep the computer awake while watching (`caffeinate -is` / `SetThreadExecutionState`), so it can't fall asleep on its own first. |
| `GOOD_NIGHT_CLAUDE_DIR` | `$CLAUDE_CONFIG_DIR/projects` or `~/.claude/projects` | Claude Code transcripts. |
| `GOOD_NIGHT_CURSOR_DIR` | `~/.cursor/projects` | Cursor transcripts. |
| `GOOD_NIGHT_CODEX_DIR` | `$CODEX_HOME` or `~/.codex` | Codex sessions (uses `$CODEX_HOME` if set). |
| `GOOD_NIGHT_LOG` | `~/Library/Logs/good-night.log` (Windows: `%LOCALAPPDATA%\good-night\good-night.log`) | Log file (also printed to stderr). |

Example: check every 30s, need 10 quiet checks (5 min), 2-minute countdown,
then shut down instead of sleeping:

```sh
GOOD_NIGHT_INTERVAL=30 GOOD_NIGHT_IDLE_CHECKS=10 GOOD_NIGHT_GRACE=120 \
  GOOD_NIGHT_ACTION=shutdown good-night watch
```

## One-time setup on macOS (all prompts happen here)

```sh
./bin/good-night setup                    # for the default action, sleep
./bin/good-night setup --action shutdown  # if you use GOOD_NIGHT_ACTION=shutdown
```

Setup only asks for what the action needs. It goes through four steps:

1. **sudo rule (`shutdown` only).** Runs `scripts/setup-sudo.sh`, which asks
   for your login password once in the terminal (details below). `sleep` and
   `display-off` use `pmset`, which needs no sudo, so this is skipped.
2. **Graceful-quit permission (`shutdown` only, optional).** For each app open right now,
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
PASS  action              shutdown (GOOD_NIGHT_ACTION/--action; macOS default is sleep) -> /usr/bin/sudo -n /sbin/shutdown -h now
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
| action | known and supported on this OS | set `GOOD_NIGHT_ACTION` / `--action` |
| sudo rule (`shutdown` only) | `sudo -n -l /sbin/shutdown -h now`; INFO "not needed" for other actions | `setup --action shutdown`, or `scripts/setup-sudo.sh` |
| notifications | Notification Center prefs (`com.apple.ncprefs.plist`) list Script Editor, or `setup` already ran | `setup`, or `GOOD_NIGHT_NOTIFY=0` |
| quit apps (`shutdown` only) | `AEDeterminePermissionToAutomateTarget(..., askUserIfNeeded=false)`: granted means AppleScript quit, while not asked, denied (-1743) or would-prompt (-1744) means SIGTERM | never fails. Change grants in System Settings > Privacy & Security > Automation > *your terminal* |
| agent data | `listdir` on `~/.claude/projects`, `~/.cursor/projects` | System Settings > Privacy & Security > Files & Folders |
| dialog, caffeinate | need no permission | n/a |

`watch` runs the same checks at startup. If any check is FAIL, it logs why
and exits with code 2 before it starts watching, so nothing can prompt or
fail while you're asleep. In `DRY_RUN`, a missing sudo rule is only a warning.

### The sudoers rule (only for `GOOD_NIGHT_ACTION=shutdown`)

`shutdown` needs root; `sleep` and `display-off` don't. `good-night` runs `sudo -n` (non-interactive) and
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

## How `shutdown` works on macOS

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

## Windows

Works with the same commands. Nothing needs Administrator rights or shows a
UAC prompt, so `setup` only shows a sample countdown popup and runs `doctor`.

| Piece | How it's done on Windows |
|---|---|
| default action | `hibernate` (`shutdown /h`): nothing is lost, uses no power, survives a flat battery |
| countdown | a `WScript.Shell` popup with OK (now) / Cancel and a timeout, plus a console countdown; `good-night cancel` and Ctrl-C work too |
| keep awake while watching | `SetThreadExecutionState(ES_CONTINUOUS \| ES_SYSTEM_REQUIRED)`, cleared before the action |
| `shutdown` | every app window gets `CloseMainWindow()` (like clicking X; apps may save state), then after `GOOD_NIGHT_QUIT_WAIT` seconds `shutdown /s /t 0 /f`. Without `/f` one unsaved document would park Windows on "This app is preventing shutdown" all night, so unsaved work in an app that's still open is lost (same as macOS). |
| agent data | `%USERPROFILE%\.claude\projects` and `%USERPROFILE%\.cursor\projects` (override with `GOOD_NIGHT_CLAUDE_DIR` / `GOOD_NIGHT_CURSOR_DIR`) |
| logs / state | `%LOCALAPPDATA%\good-night\` |

**Hibernation must be enabled** for the default action. `doctor` reads
`powercfg /a` (and the `HibernateEnabled` registry value, for non-English
Windows) and fails clearly if it's off. To fix, either enable it once from an
**Administrator** terminal:

```powershell
powercfg /hibernate on
```

or use sleep instead: `$env:GOOD_NIGHT_ACTION="sleep"`.

**Sleep vs hibernate caveats:**

- The common one-liner `rundll32 powrprof.dll,SetSuspendState 0,1,0`
  **hibernates** whenever hibernation is on (rundll32 passes garbage
  arguments). good-night calls `SetSuspendState(Hibernate=FALSE, ...)`
  directly through `ctypes`, so `sleep` really sleeps.
- On **Modern Standby (S0)** laptops, `powercfg /a` shows "Standby (S0 Low
  Power Idle)" instead of S3. `SetSuspendState` may not put those to sleep
  reliably, and S0 "sleep" can keep doing network work and drain battery.
  `doctor` warns about this; prefer `hibernate` there.
- If **Hybrid Sleep** is on (common on desktops), sleep also writes a
  hibernation file, which is fine.
- Wake timers and devices allowed to wake the PC (`powercfg /waketimers`,
  `powercfg /devicequery wake_armed`) can wake it again; that's Windows
  configuration, good-night leaves it alone.

### Testing on Windows

1. Install Python 3.9+ from [python.org](https://www.python.org/downloads/)
   (tick "Add python.exe to PATH") or `winget install Python.Python.3.12`.
2. Get the code:

   ```powershell
   git clone https://github.com/jazelly/good-night.git
   cd good-night
   ```

3. Look around (all of these are read-only):

   ```powershell
   python bin\good-night actions      # action matrix, default for this PC
   python bin\good-night status       # what the detectors see
   python bin\good-night doctor       # checks for the default action (hibernate)
   python bin\good-night doctor --action sleep
   ```

   `bin\good-night.cmd status` and `python -m good_night status` (from the
   repo folder) work too.

4. Rehearse a whole night without acting (PowerShell syntax):

   ```powershell
   $env:DRY_RUN=1; $env:GOOD_NIGHT_IDLE_CHECKS=1; $env:GOOD_NIGHT_INTERVAL=10; $env:GOOD_NIGHT_GRACE=15
   python bin\good-night watch
   # you should see the countdown popup, then "DRY RUN: would run: ...shutdown.exe /h"
   Remove-Item Env:DRY_RUN, Env:GOOD_NIGHT_IDLE_CHECKS, Env:GOOD_NIGHT_INTERVAL, Env:GOOD_NIGHT_GRACE
   ```

   (cmd.exe: `set DRY_RUN=1` then `python bin\good-night watch`.)

5. Try each action for real, with nothing important open:

   ```powershell
   python bin\good-night watch --action display-off   # quickest to test
   python bin\good-night watch --action sleep
   python bin\good-night watch --action hibernate
   ```

6. Run the unit tests: `python -m unittest discover -s tests -v`.

The log is at `%LOCALAPPDATA%\good-night\good-night.log`.

## How idle detection works

Each agent has a **detector** that lists recent sessions and labels each one
`busy`, `idle`, or `info` (shown but never blocking). The action runs only
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

### Codex

Both Codex CLI and the Codex Desktop app write rollout transcripts as JSONL
files under `$CODEX_HOME/sessions/YYYY/MM/DD/rollout-*.jsonl` (default
`~/.codex/sessions/`). Each line is a JSON object with `type`, optional
`payload`, and `timestamp`:

| Event | State |
|---|---|
| `type == "event_msg"`, `payload.type == "task_started"` | busy: turn started |
| `type == "event_msg"`, `payload.type == "task_complete"` | idle: turn finished |
| `type == "event_msg"`, `payload.type == "turn_aborted"` | idle: turn aborted |
| `type == "response_item"` with tool calls/outputs | busy: tool running or model working |

A session in progress has `task_started` and subsequent `response_item` records
without a following `task_complete` or `turn_aborted`. Activity time comes from
record timestamps (or file mtime if no timestamps). The detector reads the head
of each file for session metadata (`cwd`, `agent_nickname`) and the tail for
latest state, avoiding loading multi-MB files entirely.

Subagent sessions (with `parent_thread_id` / `agent_nickname`) are tracked with
clear labels.

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

- [x] **Codex CLI** detector (`~/.codex/sessions/**/rollout-*.jsonl`)
- [ ] Optional process-tree check for agent-spawned background jobs
- [ ] `launchd` agent / `good-night arm` for "shut down tonight after ..."
- [x] Sleep / hibernate / display-off instead of shutdown (`GOOD_NIGHT_ACTION`)
- [x] Windows support
- [ ] Homebrew formula

## License

MIT
