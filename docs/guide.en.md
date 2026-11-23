# Guide

[Русский](guide.md) · **English**

Datum is an AI-first engineering editor: an AI agent handles the parts,
assemblies, mechanisms and calculations, and the human steers. This guide
covers installing it, setting tasks for the agent and what lies under the
hood.

- [Requirements](#requirements)
- [Installation](#installation)
- [Running](#running)
- [Working with the agent](#working-with-the-agent)
- [Your first project](#your-first-project)
- [The window](#the-window)
- [Saving and the `.datum` archive](#saving-and-the-datum-archive)
- [Other people's models](#other-peoples-models)
- [Export and slicing](#export-and-slicing)
- [Mechanisms and simulation](#mechanisms-and-simulation)
- [Development](#development)
- [Troubleshooting](#troubleshooting)

The interface and messages are in Russian; this guide quotes them with an
English translation where it helps.

## Requirements

| What | Why | Required |
|---|---|---|
| Linux, x86-64 | Datum is developed and tested there | yes |
| Python **3.11** | backend; the dependency lock is built for 3.11 exactly | yes |
| Node.js 22.12+ and npm | the window (Electron 44, Vite) | yes |
| a C++ compiler, `make`, Python 3 | building the `node-pty` terminal module | yes |
| [Claude Code](https://claude.com/claude-code) — or another MCP-capable agent | the agent does the work; the window, skill and critic are built for Claude Code | yes |
| ElegooSlicer | slicing from Datum (`datum slice`) | no |

Expect about 2–3 GB on disk: Electron, the fTetWild mesher, MuJoCo and (on
x86-64) Intel MKL for the fast FEM solver — about 700 MB on its own.

If your system Python is not 3.11, install 3.11 alongside without touching the
system one, e.g. with [uv](https://docs.astral.sh/uv/) (`uv python install 3.11`,
then use `$(uv python find 3.11)` instead of `python3.11`) or pyenv.

## Installation

```sh
git clone https://github.com/HelpFreedom/Datum.git
cd Datum
```

**1. Python environment.**

```sh
python3.11 -m venv .venv
.venv/bin/pip install --require-hashes -r py/requirements.lock
.venv/bin/pip install --no-deps -e py
```

The first command installs exactly the versions and hashes recorded in
`py/requirements.lock`: a tampered package will not install. The second
installs Datum itself in development mode (`-e`) — edits in `py/` take effect
immediately.

**2. The window.**

```sh
scripts/bootstrap.sh
npm run build
```

`scripts/bootstrap.sh` installs the npm packages with install scripts
**disabled** (`.npmrc`) and explicitly builds the two packages that need them:
`esbuild` and `node-pty`, then downloads the Electron binary (about 100 MB,
verified against checksums). It ends by checking that everything loads.
`npm run build` builds the window (`apps/ui`) and the shell (`apps/shell`).

**3. Check.**

```sh
.venv/bin/python -m datum.cli status
npm start -- examples/latch
```

A window with the box and lid of the `latch` example should open.

**4. Optional.**

```sh
make install   # symlink ~/.local/bin/datum → scripts/datum
make mcp       # .mcp.json at the repository root
```

After `make install` the `datum` command works in a terminal, dmenu and
application menus (if `~/.local/bin` is on `PATH`). It is a symlink, not a
copy: repository updates are picked up immediately.

**Updating.** `git pull`, then repeat steps 1 and 2 (if `py/requirements.lock`
or `package-lock.json` changed) and `npm run build`.

## Running

| Command | What it does |
|---|---|
| `datum` | window with an empty "Untitled" project (`~/.local/share/datum/untitled/`) |
| `datum <directory>` | window with that project |
| `datum <file.datum>` | unpack the archive next to it and open it |
| `datum <command> …` | the regular CLI: `datum check …`, `datum export …`, `datum --help` |

Without `make install`: `.venv/bin/python -m datum.cli <command> …` and
`npm start -- <project or archive>`.

The window detaches from the terminal; its log is
`~/.local/state/datum/window.log`. A start-up error launched from dmenu is
shown as a notification (`notify-send`).

CLI exit codes: `0` — passed, `1` — checks failed, `2` — bad arguments, `3` —
the operation refused (no project, an error in a script), `130` — interrupted.

## Working with the agent

Datum is an editor for an AI agent: the agent designs and computes, the human
sets the task, answers questions and decides when it is done. It is designed
around [Claude Code](https://claude.com/claude-code).

**Setup.** Install Claude Code so that `claude` is on `PATH` (or at
`~/.local/bin/claude`; any other path — via the `DATUM_CLAUDE` variable) and
sign in once in a terminal. In the Datum window the agent is started with the
"Запустить Claude Code" (Start Claude Code) button in the bottom panel — it
does not start by itself: opening the window is not yet calling the agent.

**How to set a task.** Talk about the intent, not the commands:

- what it is and what it is for: "a wall holder for two screwdrivers", "an arm
  that lifts 100 g at 15 cm";
- what to make it with: material, printer or manufacturing method, purchased
  parts (a servo, a bearing, screws) — with a model or datasheet if you have
  one;
- what is known for sure and what is not: "I measured the diameter with a
  caliper — 8.1 mm" becomes a `measured` parameter with a source; the agent
  declares unknowns as guesses with a range and tells you whether they are
  worth measuring;
- ask it to show you: the agent puts a part, a section or a caption right into
  the window, and you get your own view back with one button;
- point at things: select a part, hover the caliper and say "here" — the agent
  sees what is selected and where you are looking from.

**What the built-in agent gets:**

- every Datum operation as an MCP tool (`mcp__datum__*`) — the same as the CLI
  and the window's buttons;
- a skill with design rules (`plugin/skills/datum`), loaded as a plugin
  (`--plugin-dir plugin`) in any project;
- a view of the window: what is selected and hidden, where the camera looks,
  what the caliper picked (`window_state`), the whole bed as a picture
  (`scene_view`); it can also show you a part, a section or a caption
  (`window_show`);
- an AI critic (`plugin/agents/critic.md`): when the project is done, a fresh
  agent with read-only tools looks for what was missed (at most two rework
  rounds), and the `/datum:audit` command — an independent audit by topic;
- the project's decision log (`JOURNAL.md`): a new session receives a project
  summary by itself and picks up where you left off.

**Without the window.** `claude` in a plain terminal sees the tools too:
inside a project — through the `.mcp.json` written by `datum init`; in the
repository — after `make mcp`. If the window is open, the agent's edits show
up in it immediately.

**Another agent.** Which agent to use is your choice: any MCP-capable agent
connects to the same operations with `datum mcp --stdio` (or
`.venv/bin/python -m datum.cli mcp --stdio`). The window, the skill, the
critic and the hooks are built for Claude Code; give another agent the skill's
rules yourself — they are in `plugin/skills/datum/SKILL.md` and
`references/`.

## Your first project

Normally the agent does all of this when you ask. Here is the same by hand,
to show what lies under the hood: plain files and commands.

```sh
datum init ~/parts/plate
```

This creates an empty project: `datum.toml` (printer, bed, material),
`params/base.yaml`, `checks/checks.yaml`, `NOTES.md` and an empty `parts/`.

**Parameters.** Append to `params/base.yaml`:

```yaml
PLATE_W: {value: 40.0, unit: mm, provenance: chosen, reason: "plate width"}
PLATE_T: {value: 3.0, unit: mm, provenance: chosen, reason: "thickness"}
HOLE_D:
  value: 3.4
  unit: mm
  provenance: assumed
  range: [3.2, 3.6]
  reason: "for an M3 screw, fit not tested on this printer"
```

Every number has a provenance:

| `provenance` | meaning | required next to it |
|---|---|---|
| `measured` | measured | `source` — with what and how |
| `assumed` | a guess | `range` — where it may lie |
| `derived` | computed by a formula | `expr` |
| `standard` | from a standard or a datasheet | `ref` |
| `chosen` | a designer's decision | `reason` |

**A part.** Create `parts/plate.py`:

```python
"""A plate with a hole for an M3 screw."""

from datum.build import P, centre_on_bed, part
from datum.geom import box, cyl_z, diff


@part("plate", print_up="+Z")
def build():
    w = P.PLATE_W
    plate = box(-w / 2, w / 2, -w / 2, w / 2, 0, P.PLATE_T)
    hole = cyl_z(P.HOLE_D, -1, P.PLATE_T + 1)
    return centre_on_bed(diff(plate, [hole]))
```

- `P.NAME` is a parameter value; Datum records which parameters a part reads;
- `print_up="+Z"` is the side facing up when printed; printability checks take
  the pose from here and never choose it themselves;
- `centre_on_bed` puts the part in the middle of the bed.

The full API: `plugin/skills/datum/references/api.md` (generated from the code).

**Build, check, export.**

```sh
datum build  ~/parts/plate
datum check  ~/parts/plate
datum sweep  ~/parts/plate
datum export ~/parts/plate
```

`check` runs the baseline printability checks (wall, cavities, bed fit,
overhangs) plus everything declared in `checks/checks.yaml` (catalogue:
`datum checks`). `sweep` runs each `assumed` number across its range and tells
which one the verdict depends on — that is what to measure first. `export`
writes 3MF and STL to `output/` in the print pose; "не готов к печати" (not
ready to print) is the first line of the report if any check failed.

Open the project in the window: `datum ~/parts/plate`. Edit the script in any
editor — the window notices the file change by itself.

## The window

- **Top** — the menu, project state ("собирается…" building, "не прошло
  проверок: 2" two checks failed), the "Проверить" (Check) button, the archive
  dot (green — saved).
- **Left** — the scene (parts, visibility, assembly order, trash), imported
  models, versions.
- **Right** — properties of the selected part, checks worst first, parameters
  with provenance (editable right there).
- **Bottom** — Claude Code and the call feed.

The viewport has a view cube, X/Y/Z section, explode, and tools on the left.
The caliper (`D`) highlights a vertex, edge, circle or arc, hole centre or a
face; clicking a circle gives Ø, a second feature gives a distance (with ΔX ΔY
ΔZ) or an angle; `Shift` picks a point on the surface. Move (`M`, 1 mm steps,
0.1 with `Shift`) and rotate (`R`, 15° steps, 1° with `Shift`) edit the
project: the placement is written to `placement.yaml`, and checks see the part
in the same place. `Delete` removes a part entirely (with its model and
checks) into the project trash, `Ctrl+Z` brings it back. All keys: `?`.

The camera works like a slicer's: the left button orbits around the bed's
vertical, right or middle pans, the wheel zooms towards the cursor with a
constant step (finer with `Shift`), `H` resets the view.

## Saving and the `.datum` archive

A project's workspace is a folder, and every edit is written to it at once. A
`.datum` archive is the project as one file — to keep, send and reopen:

- "Файл → Сохранить как…" (File → Save as, `Ctrl+Shift+S`) — pack into a new file;
- "Сохранить" (Save, `Ctrl+S`) — to where the project was opened from or last saved;
- "Файл → Проекты…" (File → Projects, `Ctrl+O`) — recent projects, rename,
  "new from this";
- `datum file.datum` or "Открыть архив .datum…" (Open .datum archive) — unpack
  next to it and open.

The `.datum/` cache, `output/` exports and symlinks are not archived. From the
command line: `datum save <project> --dest file.datum`,
`datum unpack file.datum`. Unpacking someone else's archive checks paths,
links and size before writing anything.

Versions: `datum snapshot <project> <tag>` takes a version; `datum diff` and
the "Версии" (Versions) tab show what changed — in numbers and as a shape
picture.

## Other people's models

"Открыть файл…" (Open file) or "Файл → Импортировать модель…" (Import model)
puts a 3MF/STL on the bed: every object of the file becomes its own part
(`parts/<name>.py`, the model in `imported/`). 3MF is read the way the slicer
prints it (Bambu, Orca, Elegoo): negative parts are subtracted, modifiers are
skipped.

A reference model (e.g. a scan of an object to make a cradle for) is
`datum import`: it is shown translucent and does not take part in checks.
`datum footprint` builds the object's outline in plan, `datum diagnose` /
`datum repair` diagnose and repair a foreign mesh.

## Export and slicing

`datum export <project>` writes 3MF and STL in the print pose. The written
file is read back and compared with the part; the 3MF carries the print
recipe.

`datum slice <project>` slices with the ElegooSlicer CLI and writes a project
that the slicer's window opens as its own. The slicer is found as
`elegooslicer` on `PATH` or via `DATUM_ELEGOO_SLICER`; profiles — in the
slicer's system directory or via `DATUM_ELEGOO_PROFILES`. The result is
checked against the G-code footer (profiles, bed type, overrides), and
`datum slice-report` parses the G-code layer by layer and finds unsupported
islands.

Also: `datum mass` (mass from print settings, verified against G-code),
`datum bom` (bill of materials), `datum guide` (a picture assembly guide),
`datum coupon` (a test coupon for a fit).

## Mechanisms and simulation

A mechanism is declared in a part script (`@mechanism`, links, joints,
actuators, scenarios) — see `examples/arm` and
`plugin/skills/datum/references/simulation.md`.

```sh
datum sim-list   examples/arm               # mechanisms and scenarios
datum sim        examples/arm рука перенос  # torque, lag, stability, collisions
datum sim-stress examples/arm рука перенос  # link stresses at the worst moment (FEM)
datum sim-export examples/arm рука          # animated GLB, MJCF, URDF
```

(`рука` is the mechanism "arm", `перенос` the scenario "carry".) In the
window, the "Симуляция" (Simulation) button opens a player with torque plots
and a load map — that is what the animation in the README shows.

Friction, efficiency, backlash and rotor inertia are only accounted for when
declared, with a source; when they are not, the report says so: "запас
оптимистичен" (the margin is optimistic).

## Development

```sh
make check   # everything before a commit: backend and window tests, budgets, FEM self-check
make ui      # live window scenarios (needs a display)
make audit   # dependency inventory: 0 clean, 1 findings, 2 incomplete report (no network)
```

Separately: `cd py && ../.venv/bin/python -m pytest tests -q` — backend,
`npm test` — types and vitest for the window and the shell,
`.venv/bin/python scripts/budget.py` — time budgets.

The `py/tests/corpus` tests run on an external corpus of earlier projects
(`DATUM_CORPUS`); without it they are skipped. The slicing test with a real
slicer is skipped if ElegooSlicer is not found.

Updating the dependency lock (slow: wheel hashes for every platform are
computed):

```sh
cd py && pip-compile --generate-hashes --strip-extras --extra server --extra mcp \
  --extra mesh --extra sim --extra dev -o requirements.lock pyproject.toml
```

The whole design — [architecture.md](architecture.md), protection —
[security.md](security.md) (both in Russian), rules for changes —
[CONTRIBUTING.md](../CONTRIBUTING.md).

## Troubleshooting

**`pip` complains about hashes or cannot find a version.** The environment was
not created with Python 3.11. Delete `.venv` and recreate it with
`python3.11 -m venv .venv`.

**`scripts/bootstrap.sh` fails on `node-pty`.** No compiler: install
`base-devel` (Arch), `build-essential` (Debian/Ubuntu) or `gcc-c++ make`
(Fedora).

**`datum: нет Electron`** (no Electron) — `scripts/bootstrap.sh` was not run.
**`datum: окно не собрано`** (window not built) — `npm run build` was not run.

**The window does not open; the log mentions the Chromium sandbox**
(`SUID sandbox helper`). Some distributions (e.g. Ubuntu 24.04) deny Electron
unprivileged user namespaces. Datum neither disables the sandbox nor suggests
doing so; set up the sandbox helper the way Electron requires:

```sh
sudo chown root node_modules/electron/dist/chrome-sandbox
sudo chmod 4755 node_modules/electron/dist/chrome-sandbox
```

**The agent does not start.** `claude` is not on `PATH` nor in
`~/.local/bin`, or you have never signed in to Claude Code in a terminal.

**Anything else.** The window log is `~/.local/state/datum/window.log`, the
backend state — `datum status`. An issue with steps to reproduce helps a lot.
