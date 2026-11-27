# Всё, что проверяется перед коммитом, — одной командой: `make check`.
#
# CI нет: его роль играет этот файл.
# Окно проверяется сценариями в живом Electron — им нужен дисплей, и в `check`
# они не входят; `make ui` гоняет их отдельно.

PY      := .venv/bin/python
SCRATCH := $(or $(TMPDIR),/tmp)/datum-make

.PHONY: check test-py test-ui build budget fem ui audit install mcp

check: test-py test-ui budget fem

test-py:
	cd py && ../$(PY) -m pytest tests -q -p no:warnings

test-ui:
	npm test

build:
	npm run build

budget:
	$(PY) scripts/budget.py

fem:
	cd py && ../$(PY) -m datum.cli fem-verify 3000

# Сценарии окна. `live` правит файлы проекта — поэтому идёт на копии. Проект
# называется явно: без него окно открыло бы запомненный — тот, над которым
# человек сейчас работает, — и проверка зависела бы от его дел.
ui: build
	mkdir -p $(SCRATCH)
	DATUM_SCENARIO=scripts/ui/security.cjs npm start -- examples/latch
	DATUM_SCENARIO=scripts/ui/snapshot.cjs DATUM_OUT=$(SCRATCH)/snapshot.png npm start -- examples/latch
	DATUM_SCENARIO=scripts/ui/explode.cjs DATUM_OUT=$(SCRATCH)/explode.png npm start -- examples/latch
	DATUM_SCENARIO=scripts/ui/resize.cjs DATUM_OUT=$(SCRATCH)/resize.png npm start -- examples/latch
	DATUM_SCENARIO=scripts/ui/camera.cjs DATUM_OUT=$(SCRATCH)/camera.png npm start -- examples/latch
	DATUM_SCENARIO=scripts/ui/switch.cjs npm start -- examples/latch
	rm -rf $(SCRATCH)/import && $(PY) -m datum.cli init $(SCRATCH)/import
	DATUM_SCENARIO=scripts/ui/import.cjs DATUM_OUT=$(SCRATCH)/import.png npm start -- $(SCRATCH)/import
	rm -rf $(SCRATCH)/latch-joint && cp -r examples/latch $(SCRATCH)/latch-joint && rm -rf $(SCRATCH)/latch-joint/.datum
	printf 'joints:\n  крышка: {kind: prismatic, axis: "+Z", range: [0, 15]}\n' >> $(SCRATCH)/latch-joint/assembly.yaml
	DATUM_SCENARIO=scripts/ui/joints.cjs DATUM_OUT=$(SCRATCH)/joints.png DATUM_JOINT=крышка DATUM_JOINT_U=12 npm start -- $(SCRATCH)/latch-joint
	rm -rf $(SCRATCH)/latch-versions && cp -r examples/latch $(SCRATCH)/latch-versions && rm -rf $(SCRATCH)/latch-versions/.datum $(SCRATCH)/latch-versions/snapshots
	$(PY) -m datum.cli snapshot $(SCRATCH)/latch-versions v1 >/dev/null
	$(PY) -m datum.cli set $(SCRATCH)/latch-versions CASE_H 24 >/dev/null
	$(PY) -m datum.cli snapshot $(SCRATCH)/latch-versions v2 >/dev/null
	DATUM_SCENARIO=scripts/ui/versions.cjs DATUM_OUT=$(SCRATCH)/versions.png npm start -- $(SCRATCH)/latch-versions
	rm -rf $(SCRATCH)/latch-show && cp -r examples/latch $(SCRATCH)/latch-show && rm -rf $(SCRATCH)/latch-show/.datum
	DATUM_SCENARIO=scripts/ui/show.cjs DATUM_OUT=$(SCRATCH)/show.png npm start -- $(SCRATCH)/latch-show
	rm -rf $(SCRATCH)/claude-config
	CLAUDE_CONFIG_DIR=$(SCRATCH)/claude-config DATUM_CLAUDE=$(CURDIR)/scripts/ui/fake-claude.sh DATUM_SCENARIO=scripts/ui/crash.cjs npm start -- examples/latch
	DATUM_CODEX=$(CURDIR)/scripts/ui/fake-codex DATUM_SCENARIO=scripts/ui/codex.cjs npm start -- examples/latch
	rm -rf $(SCRATCH)/latch-death && cp -r examples/latch $(SCRATCH)/latch-death && rm -rf $(SCRATCH)/latch-death/.datum
	DATUM_SCENARIO=scripts/ui/server_death.cjs npm start -- $(SCRATCH)/latch-death
	rm -rf $(SCRATCH)/projects && mkdir -p $(SCRATCH)/projects/datum/untitled
	cp -r examples/latch $(SCRATCH)/projects/datum/untitled/коробка && rm -rf $(SCRATCH)/projects/datum/untitled/коробка/.datum
	$(PY) -m datum.cli init --name "Второй" $(SCRATCH)/projects/datum/untitled/второй >/dev/null
	XDG_DATA_HOME=$(SCRATCH)/projects DATUM_SCENARIO=scripts/ui/projects.cjs DATUM_OUT=$(SCRATCH)/projects.png npm start -- $(SCRATCH)/projects/datum/untitled/коробка
	rm -rf $(SCRATCH)/latch-live && cp -r examples/latch $(SCRATCH)/latch-live
	rm -rf $(SCRATCH)/latch-live/.datum
	DATUM_SCENARIO=scripts/ui/live.cjs DATUM_OUT=$(SCRATCH)/live npm start -- $(SCRATCH)/latch-live
	rm -rf $(SCRATCH)/arm-sim && cp -r examples/arm $(SCRATCH)/arm-sim && rm -rf $(SCRATCH)/arm-sim/.datum
	DATUM_SCENARIO=scripts/ui/sim.cjs DATUM_OUT=$(SCRATCH)/sim.png npm start -- $(SCRATCH)/arm-sim
	rm -rf $(SCRATCH)/arm-launch && cp -r examples/arm $(SCRATCH)/arm-launch && rm -rf $(SCRATCH)/arm-launch/.datum
	DATUM_SCENARIO=scripts/ui/sim_launch.cjs DATUM_OUT=$(SCRATCH)/launch.png npm start -- $(SCRATCH)/arm-launch

# Сети может не быть: код 2 — «отчёт неполон», и это не «чисто».
audit:
	$(PY) scripts/audit.py

# `.mcp.json` в корне — чтобы `claude`, запущенный в репозитории, видел
# инструменты Datum. Путь к интерпретатору у каждого свой, поэтому файл не
# хранится в git, а пишется здесь.
mcp:
	$(PY) -c 'import json, os, sys; root = os.getcwd(); json.dump({"mcpServers": {"datum": {"command": os.path.join(root, ".venv/bin/python"), "args": ["-m", "datum.cli", "mcp", "--stdio"], "env": {"PYTHONPATH": os.path.join(root, "py")}}}}, open(".mcp.json", "w"), indent=2); print("→ .mcp.json")'

# Команда `datum` на PATH — для dmenu, меню и терминала. Ссылка, а не копия:
# лаунчер находит репозиторий по своему настоящему пути.
BIN := $(HOME)/.local/bin

install: build
	mkdir -p $(BIN)
	ln -sfn $(CURDIR)/scripts/datum $(BIN)/datum
	@echo "datum → $(BIN)/datum"
