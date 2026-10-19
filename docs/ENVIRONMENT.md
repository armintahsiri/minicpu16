# Environment and tool versions

Recorded at the start of Phase 4 (2026-10-20, Asia/Tehran).

| Tool | Version | Notes |
|---|---|---|
| OS | macOS 27.0 (build 26A428), arm64 | Apple Silicon |
| Python | 3.12.8 | standard library only, no pip packages |
| NVC | 1.23.0 (LLVM 23.1.1) | `/opt/homebrew/bin/nvc`, VHDL-2008 (`--std=08`) |
| GHDL | 6.0.0 (Dunoon edition), LLVM backend | `/tmp/ghdl-llvm-6.0.0-macos15-aarch64/bin/ghdl` |
| GNU Make | 3.81 | Apple's make |
| Git | 2.54.0 (Apple Git-157) | local identity set in this repo |

GHDL note: the path quoted in the brief did not exist when this phase started.
The official LLVM 6.0.0 macOS aarch64 archive was downloaded from the GHDL
GitHub release and extracted to the same path, so `make vhdl-check` uses it
without further installation. On other machines, override with
`make GHDL=/path/to/ghdl ...`; the Makefile falls back to `ghdl` on `PATH`.

The reference MiniCPU-8 repository (a separate project) was read-only
during this phase and was not modified.

## Phase 5A (2026-10-20)

GHDL was again missing from PATH; the same official LLVM 6.0.0 macOS aarch64
archive was downloaded to `/tmp/ghdl-llvm-6.0.0-macos15-aarch64`.  The Makefile
defaults to it when `ghdl` is not on PATH.

| Tool | Version / path | Notes |
|---|---|---|
| NVC | 1.23.0 (LLVM 23.1.1) | `/opt/homebrew/bin/nvc` |
| GHDL | 6.0.0 | `/tmp/ghdl-llvm-6.0.0-macos15-aarch64/bin/ghdl` |
| Python | 3.12.8 | standard library only |
| GNU Make | 3.81 | Apple's make |
| Git | 2.54.0 | local identity unchanged |
| Yosys | 0.70+post | `/opt/homebrew/bin/yosys` |
| ghdl-yosys-plugin | tag `ghdl-v6.0.0`, built locally | `/tmp/ghdl-yosys-plugin/ghdl.so` |
| nextpnr-ice40 | 0.11.1 | `/opt/homebrew/bin/nextpnr-ice40` |
| icestorm | 1.1 | `icepack`/`iceprog` installed |

Synthesis plugin note: set `GHDL_PREFIX=/tmp/ghdl-llvm-6.0.0-macos15-aarch64/lib/ghdl`
(the GHDL **library** prefix, not the install root), otherwise GHDL looks for
`<prefix>/ieee/v08` and cannot find the `std` library.  The plugin was built with
`make GHDL=/tmp/ghdl-llvm-6.0.0-macos15-aarch64/bin/ghdl`.

Synthesis smoke is an estimate only: `synth_ice40`, then nextpnr with seed 1 and
target 50 MHz.  No hardware was programmed.
