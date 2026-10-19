# MiniCPU-16 build and verification (Phase 5A)
#
#   make test        Phase 4 checks + leaf RTL testbenches (NVC and GHDL) +
#                    quick differential suite + quick fuzz.  Fails loudly.
#   make rtl-test    leaf testbenches and the full differential suite, both sims
#   make rtl-fuzz    full 300-program fuzz on NVC + fixed 50-program GHDL subset
#   make mutation    the 12-mutation verification check
#   make synth-smoke Yosys/GHDL inference check (time-boxed; needs the plugin)
#   make bench       print the benchmark table and regenerate docs/BENCHMARKS.md
#   make clean       remove build/, work/ and __pycache__
#
# GHDL is the second simulator.  The default below finds it on PATH (CI) and
# otherwise uses the official 6.0.0 LLVM archive in /tmp (this machine).
# Override with: make GHDL=/path/to/ghdl test

NVC       ?= nvc
GHDL_BUNDLED := /tmp/ghdl-llvm-6.0.0-macos15-aarch64/bin/ghdl
GHDL      ?= $(shell command -v ghdl 2>/dev/null || (test -x $(GHDL_BUNDLED) && echo $(GHDL_BUNDLED)) || echo ghdl)
STD       := --std=08
BUILD     := build
NVC_DIR   := $(BUILD)/nvc
GHDL_DIR  := $(BUILD)/ghdl
PY        := python3

.PHONY: all test consistency vhdl-check asmtest simtest programstest gentest         tracetest bench bench-check clean rtl-leaf rtl-suite rtl-suite-quick         rtl-fuzz-quick rtl-test rtl-fuzz mutation synth-smoke

all: test

test: consistency vhdl-check asmtest simtest programstest gentest bench-check       rtl-leaf rtl-suite-quick rtl-fuzz-quick
	@echo ""
	@echo "==================================================="
	@echo "  ALL MINICPU-16 PHASE 5A CHECKS PASSED"
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

bench:
	@$(PY) tools/bench.py

rtl-leaf:
	@echo "=== leaf RTL testbenches: NVC and GHDL ==="
	@$(PY) tools/rtl_leaf_tests.py --sim both --workdir $(BUILD)/rtl_leaf

rtl-suite:
	@echo "=== full differential suite: reference traces, benchmarks, directed ==="
	@$(PY) tools/rtl_suite.py --sim both --workdir $(BUILD)/rtl_suite

rtl-suite-quick:
	@echo "=== quick differential suite: NVC and GHDL ==="
	@$(PY) tools/rtl_suite.py --sim both --quick --workdir $(BUILD)/rtl_suite_quick

rtl-fuzz-quick:
	@echo "=== quick differential fuzz (24 random + 12 curated, NVC; 4 GHDL) ==="
	@$(PY) tools/rtl_fuzz.py --n 24 --quick --sim nvc --ghdl-subset 4 	    --workdir $(BUILD)/rtl_fuzz_quick

rtl-test: rtl-leaf rtl-suite
	@echo "RTL full differential test complete"

rtl-fuzz:
	@echo "=== full differential fuzz: 300 random + 12 curated on NVC, 50 on GHDL ==="
	@$(PY) tools/rtl_fuzz.py --n 300 --sim nvc --ghdl-subset 50 	    --workdir $(BUILD)/rtl_fuzz

mutation:
	@echo "=== mutation check: 12 injected RTL bugs ==="
	@$(PY) tools/mutation_check.py --workdir $(BUILD)/mutation

synth-smoke:
	@echo "=== synthesis inference smoke test (Yosys + ghdl-yosys-plugin) ==="
	@$(PY) tools/synth_smoke.py --build $(BUILD)/synth

clean:
	@rm -rf $(BUILD) work
	@find . -name '__pycache__' -type d -prune -exec rm -rf {} +
	@find . -name '*.pyc' -delete
	@echo "clean: removed build/, work/ and Python caches"
