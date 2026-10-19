# MiniCPU-16 build and verification (Phase 4)
#
#   make test        consistency + negative self-test + VHDL (NVC and GHDL) +
#                    assembler/disassembler + golden model + traces + programs +
#                    benchmarks + random generator self-validation
#   make bench       print the benchmark table and regenerate docs/BENCHMARKS.md
#   make vhdl-check  analyse the constants package and a generated program
#                    package with both NVC and GHDL (VHDL-2008)
#   make clean       remove build/, work/ and __pycache__
#
# GHDL is a second, independent simulator.  The default below finds it on PATH
# (the CI job installs the distro package); on this machine the official 6.0.0
# LLVM archive is used from /tmp.  Override with: make GHDL=/path/to/ghdl test

NVC       ?= nvc
GHDL_BUNDLED := /tmp/ghdl-llvm-6.0.0-macos15-aarch64/bin/ghdl
GHDL      ?= $(shell command -v ghdl 2>/dev/null || (test -x $(GHDL_BUNDLED) && echo $(GHDL_BUNDLED)) || echo ghdl)
STD       := --std=08
BUILD     := build
NVC_DIR   := $(BUILD)/nvc
GHDL_DIR  := $(BUILD)/ghdl
PY        := python3

.PHONY: all test consistency vhdl-check asmtest simtest programstest gentest \
        tracetest bench bench-check clean

all: test

test: consistency vhdl-check asmtest simtest programstest gentest bench-check
	@echo ""
	@echo "==================================================="
	@echo "  ALL MINICPU-16 PHASE 4 CHECKS PASSED"
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

clean:
	@rm -rf $(BUILD) work
	@find . -name '__pycache__' -type d -prune -exec rm -rf {} +
	@find . -name '*.pyc' -delete
	@echo "clean: removed build/, work/ and Python caches"
