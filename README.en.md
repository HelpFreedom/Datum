# Datum

[Русский](README.md) · **English**

Designing parts for 3D printing together with an AI. A part is a Python
script, every number is a parameter with a provenance (measured, guessed,
derived), and the checks catch what you would otherwise pay for with a print:
a part that "holds" by touching instead of latching, a sealed cavity, a thin
wall, a snap fit without a lead-in chamfer, an assembly that cannot be put
together, a joint whose motor is too weak. The window shows exactly what the
checks compute, and the built-in Claude Code works through the same
operations as the human.

![Datum: robot arm simulation with a load map and checks](docs/media/demo.gif)

> A young, evolving project. Tested on Linux (x86-64). The interface, messages
> and most documentation are in Russian first.

## Features

- **A part is code, not a feature tree.** A Python script builds the solid
  with the [manifold3d](https://github.com/elalish/manifold) kernel:
  primitives, booleans, chamfers and fillets, threads, text from fonts,
  field-based sculpting, Gridfinity.
- **Numbers with provenance.** Every parameter in `params/base.yaml` is tagged
  `measured`, `assumed` (with a range), `derived`, `standard` or `chosen`.
  `datum sweep` runs the guesses across their ranges and tells you which
  unmeasured number decides the outcome and which is not worth measuring.
- **Checks before printing.** Printability in the print pose (walls,
  overhangs, bridges, cavities, bed fit), assembly along the insertion path,
  snap fits and insertion force, joint travel, machine elements (pins, screws,
  beams) and finite-element elasticity — reported as a bracket, not a single
  number.
- **The window.** Electron + three.js: a slicer-style caliper, wall thickness,
  section view, exploded view, moving and rotating parts with the mouse,
  versions with shape comparison, a printability map.
- **Mechanisms.** MuJoCo simulation: required actuator torque, stability,
  collisions against the exact geometry, link stresses; export to animated GLB,
  MJCF and URDF.
- **The agent.** Claude Code docked in the window gets every Datum operation
  over MCP plus a skill with the rules. It sees what is selected in the window
  and where the camera looks, can show you a part or a section, and an AI
  critic reviews a finished project with fresh eyes.
- **Output.** 3MF and STL in the print pose, with the written file read back
  and verified; slicing with the ElegooSlicer CLI; mass from the print
  settings; a picture assembly guide; a bill of materials. A project is saved
  as a single `.datum` archive.

## Installation

Requirements: Linux, Python 3.11, Node.js 22.12+ with npm, a C++ compiler and
`make` (for the terminal's native module). Optional:
[Claude Code](https://claude.com/claude-code) (`claude` on `PATH`) for the
agent in the window; ElegooSlicer for slicing.

```sh
git clone https://github.com/HelpFreedom/Datum.git
cd Datum
python3.11 -m venv .venv
.venv/bin/pip install --require-hashes -r py/requirements.lock
.venv/bin/pip install --no-deps -e py
scripts/bootstrap.sh          # window dependencies; install scripts allowed by name only
npm run build
```

Optional:

```sh
make install    # the `datum` command in ~/.local/bin (terminal, dmenu, menus)
make mcp        # .mcp.json — Datum tools for `claude` started in the repository
```

Every step explained, with fixes for common problems:
[the guide](docs/guide.en.md).

## Quick start

```sh
datum                     # window with an empty "Untitled" project
datum init ~/parts/plate  # a new empty project
datum ~/parts/plate       # open it in the window
datum examples/latch      # open an example
```

Without `make install`, use `.venv/bin/python -m datum.cli …` for commands and
`npm start -- <project>` for the window.

A part is a file in the project's `parts/`:

```python
from datum.build import P, centre_on_bed, part
from datum.geom import box, cyl_z, diff


@part("plate", print_up="+Z")
def build():
    w = P.PLATE_W
    plate = box(-w / 2, w / 2, -w / 2, w / 2, 0, P.PLATE_T)
    hole = cyl_z(P.HOLE_D, -1, P.PLATE_T + 1)
    return centre_on_bed(diff(plate, [hole]))
```

```sh
datum build  ~/parts/plate   # build
datum check  ~/parts/plate   # checks; exit code 1 on failure
datum sweep  ~/parts/plate   # what to measure first
datum export ~/parts/plate   # 3MF and STL in output/, in the print pose
```

A complete example with parameters is in
[the guide](docs/guide.en.md#your-first-project). All commands:
`datum --help`; they are also the agent's tools (`mcp__datum__*`).

## The window

At the top: the menu, the project state and the "Check" button. On the left:
the scene (parts, visibility, assembly order), imported models, versions and
the trash. On the right: properties of the selected part, checks (worst
first) and parameters with provenance. At the bottom: Claude Code (started by
a button) and the call feed, showing what you did and what the agent did.

| Key | Action |
|---|---|
| `D` | caliper: vertex, edge, circle (Ø), hole centre, face; two features — distance or angle |
| `T` | wall thickness (computed by the server) |
| `M` / `R` | move / rotate a part; this edits the project (`placement.yaml`) |
| `E` | explode the parts / back to assembly |
| `B` / `C` | build / check |
| `H` | reset the camera |
| `Delete`, `Ctrl+Z` | delete a part entirely (to the project trash) and restore it |
| `Ctrl+S`, `Ctrl+Shift+S`, `Ctrl+O` | save, save as, projects |
| `?` | all keys; letters work in any keyboard layout |

## Examples

| | |
|---|---|
| `examples/hello` | what `datum init` creates: an empty project |
| `examples/latch` | a box with a snap-fit lid: assembly, insertion force, sweep over guesses |
| `examples/gridfinity` | a divided bin and a baseplate for the 42 mm grid (`datum.std.gridfinity`) |
| `examples/sculpt` | a ball stand sculpted with a field: slope limit, surface pattern, draft mode |
| `examples/arm` | a two-link arm on two servos: mechanism simulation, ballast |
| `examples/dispenser`, `examples/spool` | projects ported from a "scripts + Claude Code" workflow: analytics and FEM, 168 grooves in one pipeline |

## A project on disk

```
datum.toml            printer, bed, material
params/base.yaml      parameters with provenance
parts/*.py            part scripts
lib/                  shared project helpers
assembly.yaml         assembly order, insertion method, joints
checks/checks.yaml    checks from the catalogue (`datum checks`)
NOTES.md, JOURNAL.md  reasoning and the decision log
.datum/, output/      cache and exports (not archived)
```

## Repository layout

```
py/datum/      backend: geom (kernel), build, doc, checks, fem, sim, io, viz,
               std, tools (operation registry), server, mcp
apps/shell/    Electron shell (TypeScript)
apps/ui/       the window: Vite + React + three.js
plugin/        Claude Code plugin: skill, critic, audit, hooks
examples/      example projects
scripts/       launcher, window test scenarios, time budgets, dependency audit
docs/          guide, architecture, security, GPU measurements
```

The in-depth design is in [docs/architecture.md](docs/architecture.md), what
is and is not protected — in [docs/security.md](docs/security.md) (both in
Russian).

## Tests

```sh
make check   # backend and window tests, time budgets, FEM self-check
make ui      # scenarios in the live window (needs a display)
make audit   # licences and known vulnerabilities of dependencies (needs network)
```

## Authors

- **Black Triangle** ([@HelpFreedom](https://github.com/HelpFreedom)) — author and owner
- **Claude** (Anthropic) — co-author of the code and documentation

How to propose a change — [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE). Third-party components keep their own licences: DejaVu fonts
(`py/datum/assets/fonts/LICENSE-DejaVu.txt`), Python and npm dependencies
(inventory — `make audit`, discussion — [docs/security.md](docs/security.md)).
The optional `pypardiso` dependency brings Intel MKL under the Intel
Simplified Software License.
