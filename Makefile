# MiniCPU-16 build and verification (Phase 5B)
#
#   make test        Phase 4 checks + generator diversity assertion + leaf RTL
#                    testbenches + quick cycle-exact suite (NVC and GHDL) +
#                    quick cycle-exact fuzz (incl. 8 GHDL) + FORWARDING=false
#                    architectural regression.  Fails loudly.
#   make rtl-test    leaf testbenches and the full cycle-exact suite, both sims
#                    (references, all benchmarks incl. hello, all directed tests,
#                    hello at UART 1/10/104)
#   make rtl-fuzz    full 1000-program forwarding-targeted cycle-exact fuzz on NVC
#                    plus 150 of the same programs on GHDL
#   make rtl-fuzz-general
#                    the general six-mode fuzz (300 NVC + 100 GHDL), cycles checked
#   make mutation    the mutation check (directed + fuzz-only, NVC + GHDL)
#   make synth-smoke Yosys/GHDL inference check and nextpnr estimates (time-boxed)
#   make postsynth-sim
#                    optional Step 10: gate-level simulation of the synthesized
#                    forwarding logic (Yosys generic mapping + built-in sim)
#   make bench       print the benchmark table and regenerate docs/BENCHMARKS.md
#   make clean       remove build/, work/ and __pycache__
#
# GHDL is the second simulator.  The search order is deliberately stable so a
# rebuild works on a fresh machine:
#
#   1. an explicit "make GHDL=/path/to/ghdl"
#   2. $(HOME)/tools/ghdl/bin/ghdl   (the permanent local install)
#   3. ghdl on PATH                  (what CI uses)
#   4. /tmp/ghdl-llvm-6.0.0-macos15-aarch64/bin/ghdl  (ephemeral fallback)
#
# /tmp is only a fallback: it is wiped on reboot, so a build that depends on it
# silently breaks.  If no GHDL is found, the check below fails loudly instead of
# producing a confusing "ghdl: command not found" halfway through a run.
# Override with: make GHDL=/path/to/ghdl test

NVC       ?= nvc
GHDL_HOME ?= $(HOME)/tools/ghdl
GHDL_TMP  := /tmp/ghdl-llvm-6.0.0-macos15-aarch64/bin/ghdl
GHDL      ?= $(shell test -x $(GHDL_HOME)/bin/ghdl && echo $(GHDL_HOME)/bin/ghdl || \
                       (command -v ghdl 2>/dev/null) || \
                       (test -x $(GHDL_TMP) && echo $(GHDL_TMP)))
STD       := --std=08
BUILD     := build
NVC_DIR   := $(BUILD)/nvc
GHDL_DIR  := $(BUILD)/ghdl
PY        := python3

.PHONY: all test consistency vhdl-check asmtest simtest programstest gentest         tracetest bench bench-check clean rtl-leaf rtl-suite rtl-suite-quick         diversity rtl-fuzz rtl-fuzz-quick rtl-fuzz-general rtl-test mutation         synth-smoke postsynth-sim fwd-regression

all: test

# Fail loudly and early if the second simulator is missing.  A silent fallback
# to a non-existent "ghdl" would only surface as a confusing error later.
check-ghdl:
	@if [ -z "$(GHDL)" ]; then \
	  echo "ERROR: no GHDL found." >&2; \
	  echo "  Looked for, in order:" >&2; \
	  echo "    1. an explicit override:  make GHDL=/path/to/ghdl <target>" >&2; \
	  echo "    2. \$$HOME/tools/ghdl/bin/ghdl   (preferred local install)" >&2; \
	  echo "    3. ghdl on PATH                  (CI uses the distro package)" >&2; \
	  echo "    4. $(GHDL_TMP) (ephemeral fallback)" >&2; \
	  echo "  Install it under \$$HOME/tools/ghdl, or put it on PATH." >&2; \
	  exit 1; \
	fi
	@echo "GHDL: $(GHDL)"

test: check-ghdl consistency vhdl-check asmtest simtest programstest gentest bench-check       diversity rtl-leaf rtl-suite-quick rtl-fuzz-quick fwd-regression
	@echo ""
	@echo "==================================================="
	@echo "  ALL MINICPU-16 PHASE 5B QUICK CHECKS PASSED"
	@echo "==================================================="

consistency:
	@echo "=== consistency check and negative self-test ==="
	@$(PY) tools/check_consistency.py
	@$(PY) tools/check_consistency.py --self-test

vhdl-check:
	@echo "=== VHDL-2008 analysis: NVC and GHDL ==="
	@mkdir -p $(NVC_DIR) $(GHDL_DIR)
	@$(PY) tools/assembler.py programs/bubble_sort.s --vhdl $(BUILD)/vhdl_program_pkg.vhd
	@cd $(NVC_DIR) && $(NVC) $(STD) -a ../../rtl/minicpu16_pkg.vhd ../../$(BUILD)/vhdl_program_pkg.vhd
	@echo "NVC: constants + generated program package OK"
	@$(GHDL) -a $(STD) --workdir=$(GHDL_DIR) rtl/minicpu16_pkg.vhd
	@$(GHDL) -a $(STD) --workdir=$(GHDL_DIR) $(BUILD)/vhdl_program_pkg.vhd
	@echo "GHDL: constants + generated program package OK"

asmtest:
	@echo "=== assembler tests and disassembler round-trip ==="
	@$(PY) tools/test_assembler.py

simtest:
	@echo "=== golden-model and trace-diff tests ==="
	@$(PY) tools/test_isa_sim.py
	@$(PY) tools/test_trace_diff.py

programstest:
	@echo "=== benchmark program results and layer agreement ==="
	@$(PY) tools/test_programs.py

gentest:
	@echo "=== random generator self-validation (200 programs) and reference traces ==="
	@$(PY) tools/test_random_programs.py

bench-check:
	@echo "=== benchmark assertions ==="
	@mkdir -p $(BUILD)
	@$(PY) tools/bench.py --check > $(BUILD)/bench.txt
	@echo "benchmark assertions OK (table in $(BUILD)/bench.txt)"

diversity:
	@echo "=== fuzz generator diversity assertion (>=98% overall, >=90% per mode) ==="
	@$(PY) tools/fuzz_diversity.py --json $(BUILD)/diversity.json

bench:
	@$(PY) tools/bench.py

rtl-leaf: check-ghdl
	@echo "=== leaf RTL testbenches: NVC and GHDL ==="
	@$(PY) tools/rtl_leaf_tests.py --sim both --workdir $(BUILD)/rtl_leaf

rtl-suite: check-ghdl
	@echo "=== full cycle-exact differential suite: both simulators ==="
	@$(PY) tools/rtl_suite.py --sim both --uart-cycle-tests --workdir $(BUILD)/rtl_suite

rtl-suite-quick: check-ghdl
	@echo "=== quick cycle-exact differential suite: NVC and GHDL ==="
	@$(PY) tools/rtl_suite.py --sim both --quick --workdir $(BUILD)/rtl_suite_quick

rtl-fuzz-quick: check-ghdl
	@echo "=== quick cycle-exact fuzz (40 random incl. fwd, NVC; 8 GHDL) ==="
	@$(PY) tools/rtl_fuzz.py --n 40 --quick --sim nvc --ghdl-subset 8 \
	    --check-cycles --forwarding --workdir $(BUILD)/rtl_fuzz_quick

# The coverage thresholds are NOT disabled here: the full forwarding run must
# prove that every counter is reached from RANDOM programs only, and that
# store_load_d1/d2/d3 each reach 50.  See docs/PHASE_5B_REPORT.fa.md section 7.
rtl-fuzz: check-ghdl
	@echo "=== full forwarding-targeted fuzz: 1000 NVC, 150 GHDL, cycle-exact ==="
	@$(PY) tools/rtl_fuzz.py --n 1000 --mode fwd --sim nvc --ghdl-subset 150 \
	    --check-cycles --forwarding \
	    --workdir $(BUILD)/rtl_fuzz

rtl-fuzz-general: check-ghdl
	@echo "=== general six-mode fuzz: 300 NVC, 100 GHDL, cycle-exact ==="
	@$(PY) tools/rtl_fuzz.py --n 300 --sim nvc --ghdl-subset 100 \
	    --check-cycles --forwarding --workdir $(BUILD)/rtl_fuzz_general

fwd-regression:
	@echo "=== FORWARDING=false architectural regression (trace-only) ==="
	@$(PY) tools/rtl_suite.py --sim nvc --quick --arch-only --no-forwarding \
	    --exclude-timing-dependent --workdir $(BUILD)/rtl_regression

rtl-test: rtl-leaf rtl-suite
	@echo "RTL full differential test complete"

mutation: check-ghdl
	@echo "=== mutation check: directed + fuzz-only, NVC and GHDL ==="
	@$(PY) tools/mutation_check.py --corpus 36 --ghdl-corpus 12 --check-cycles \
	    --forwarding --json $(BUILD)/mutation.json --workdir $(BUILD)/mutation

synth-smoke:
	@echo "=== synthesis inference smoke test (Yosys + ghdl-yosys-plugin) ==="
	@$(PY) tools/synth_smoke.py --build $(BUILD)/synth

postsynth-sim:
	@echo "=== post-synthesis gate-level simulation (step 10, optional) ==="
	@$(PY) tools/postsynth_sim.py --forwarding true --build $(BUILD)/postsynth

clean:
	@rm -rf $(BUILD) work
	@find . -name '__pycache__' -type d -prune -exec rm -rf {} +
	@find . -name '*.pyc' -delete
	@echo "clean: removed build/, work/ and Python caches"