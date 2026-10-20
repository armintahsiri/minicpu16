#!/usr/bin/env python3
"""Mutation check for the Phase 5A RTL.

Each mutation copies rtl/ to a temporary directory, applies one textual bug, and
runs a targeted selection of leaf/directed tests.  At least one test must fail.
A mutation that survives is a verification gap and is reported NOT DETECTED.

Usage:
  python3 tools/mutation_check.py
  python3 tools/mutation_check.py --only 11
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import rtl_run  # noqa: E402
import rtl_leaf_tests  # noqa: E402

Mutation = Dict[str, object]


def directed(name: str) -> Tuple[str, str]:
    return ("directed", name)


def leaf(entity: str) -> Tuple[str, str]:
    return ("leaf", entity)


MUTATIONS: List[Mutation] = [
    {
        "id": 1,
        "name": "register file reset gating removed",
        "file": "register_file.vhd",
        "edits": [(
            "if rst = '0' and we = '1' and waddr /= \"0000\" then",
            "if we = '1' and waddr /= \"0000\" then",
        )],
        "tests": [leaf("tb_register_file")],
    },
    {
        "id": 2,
        "name": "writes to R0 allowed",
        "file": "register_file.vhd",
        "edits": [
            ("if raddr1 = \"0000\" then", "if false then"),
            ("if raddr2 = \"0000\" then", "if false then"),
            ("if rst = '0' and we = '1' and waddr /= \"0000\" then",
             "if rst = '0' and we = '1' then"),
        ],
        "tests": [leaf("tb_register_file")],
    },
    {
        "id": 3,
        "name": "load-use stall for store DATA removed (rs2 match dropped)",
        "file": "hazard_unit.vhd",
        "edits": [(
            "if id_src2_en = '1' and id_rs2 /= \"0000\" and id_rs2 = ex_rd then\n      s2_ex := '1';\n    end if;",
            "if id_src2_en = '1' and id_rs2 /= \"0000\" and id_rs2 = ex_rd then\n      s2_ex := '0';\n    end if;",
        )],
        "tests": [directed("b_load_use_store_data"), directed("b_load_use_alu_b")],
    },
    {
        "id": 4,
        "name": "taken branch flushes only IF/ID, not ID/EX",
        "file": "minicpu16_core.vhd",
        "edits": [(
            "if ex_redirect = '1' or ex_halt = '1' or stall = '1' then\n          id_ex_valid <= '0';",
            "if ex_halt = '1' or stall = '1' then\n          id_ex_valid <= '0';",
        )],
        "tests": [directed("c_branch_shadow_store"), directed("c_branch_shadow_branch")],
    },
    {
        "id": 5,
        "name": "WB->ID write-through removed",
        "file": "register_file.vhd",
        "edits": [
            ("elsif rst = '0' and we = '1' and waddr = raddr1 and waddr /= \"0000\" then",
             "elsif false then"),
            ("elsif rst = '0' and we = '1' and waddr = raddr2 and waddr /= \"0000\" then",
             "elsif false then"),
        ],
        "tests": [directed("b_load_use_alu_a"), directed("a_store_load_d1")],
    },
    {
        "id": 6,
        "name": "LB uses zero extension instead of sign extension",
        "file": "dmem.vhd",
        "edits": [(
            "elsif q_load_type = LOAD_LB and b(7) = '1' then",
            "elsif false then",
        )],
        "tests": [directed("a_misaligned"), directed("a_sw_lb")],
    },
    {
        "id": 7,
        "name": "SB byte-bank select swapped",
        "file": "dmem.vhd",
        "edits": [
            ("if store_type = '1' or addr(0) = '0' then\n          even_bank(idx) <= wdata(7 downto 0);",
             "if store_type = '1' or addr(0) = '1' then\n          even_bank(idx) <= wdata(7 downto 0);"),
            ("if store_type = '1' or addr(0) = '1' then\n          odd_bank(idx) <= odd_wdata;",
             "if store_type = '1' or addr(0) = '0' then\n          odd_bank(idx) <= odd_wdata;"),
        ],
        "tests": [directed("a_sb_lw"), directed("a_sw_lb")],
    },
    {
        "id": 8,
        "name": "misaligned LW uses the next word instead of aligning down",
        "file": "dmem.vhd",
        "edits": [
            ("idx := a / 2;", "idx := (a + 1) / 2;"),
        ],
        "tests": [directed("a_misaligned")],
    },
    {
        "id": 9,
        "name": "HALT does not flush younger instructions",
        "file": "minicpu16_core.vhd",
        "edits": [
            ("if ex_redirect = '1' or ex_halt = '1' then\n          if_id_valid <= '0';",
             "if ex_redirect = '1' then\n          if_id_valid <= '0';"),
            ("if ex_redirect = '1' or ex_halt = '1' or stall = '1' then\n          id_ex_valid <= '0';",
             "if ex_redirect = '1' or stall = '1' then\n          id_ex_valid <= '0';"),
        ],
        "tests": [directed("d_halt")],
    },
    {
        "id": 10,
        "name": "JALR target uses the link value instead of old rs1",
        "file": "minicpu16_core.vhd",
        "edits": [(
            "jalr_target   <= std_logic_vector(unsigned(a_ex) + unsigned(id_ex_ctrl.imm));",
            "jalr_target   <= std_logic_vector(unsigned(pc_plus_1) + unsigned(id_ex_ctrl.imm));",
        )],
        "tests": [directed("i_jalr_rd_rs1"), directed("c_jalr_shadow")],
    },
    {
        "id": 11,
        "name": "store->load same address returns the OLD value (distance 1)",
        "file": "dmem.vhd",
        "edits": [
            ("  signal odd_wdata   : std_logic_vector(7 downto 0) := (others => '0');",
             "  signal odd_wdata   : std_logic_vector(7 downto 0) := (others => '0');\n"
             "  signal store_d     : std_logic := '0';\n"
             "  signal addr_d      : std_logic_vector(15 downto 0) := (others => '0');\n"
             "  signal wdata_d     : std_logic_vector(15 downto 0) := (others => '0');\n"
             "  signal store_type_d: std_logic := '0';"),
            ("  odd_wdata <= wdata(15 downto 8) when store_type = '1' else wdata(7 downto 0);\n\n  -- Even byte bank",
             "  odd_wdata <= wdata(15 downto 8) when store_type = '1' else wdata(7 downto 0);\n\n"
             "  -- Mutation: delay the store commit by one cycle.\n"
             "  process (clk)\n"
             "  begin\n"
             "    if rising_edge(clk) then\n"
             "      store_d <= store_en;\n"
             "      addr_d <= addr;\n"
             "      wdata_d <= wdata;\n"
             "      store_type_d <= store_type;\n"
             "    end if;\n"
             "  end process;\n\n  -- Even byte bank"),
            ("    variable a, idx : integer;", "    variable a, idx, ad, idxd : integer;"),
            ("    variable a, idx : integer;", "    variable a, idx, ad, idxd : integer;"),
            ("      if store_en = '1' and a < RAM_BYTES then\n        if store_type = '1' or addr(0) = '0' then\n          even_bank(idx) <= wdata(7 downto 0);\n        end if;\n      end if;",
             "      ad := to_integer(unsigned(addr_d));\n"
             "      idxd := ad / 2;\n"
             "      if store_d = '1' and ad < RAM_BYTES then\n"
             "        if store_type_d = '1' or addr_d(0) = '0' then\n"
             "          even_bank(idxd) <= wdata_d(7 downto 0);\n"
             "        end if;\n"
             "      end if;"),
            ("      if store_en = '1' and a < RAM_BYTES then\n        if store_type = '1' or addr(0) = '1' then\n          odd_bank(idx) <= odd_wdata;\n        end if;\n      end if;",
             "      ad := to_integer(unsigned(addr_d));\n"
             "      idxd := ad / 2;\n"
             "      if store_d = '1' and ad < RAM_BYTES then\n"
             "        if store_type_d = '1' then\n"
             "          odd_bank(idxd) <= wdata_d(15 downto 8);\n"
             "        elsif addr_d(0) = '1' then\n"
             "          odd_bank(idxd) <= wdata_d(7 downto 0);\n"
             "        end if;\n"
             "      end if;"),
        ],
        "tests": [directed("a_store_load_d1"), directed("a_sb_lw")],
    },
    {
        "id": 12,
        "name": "UART write while busy is not dropped",
        "file": "mmio.vhd",
        "edits": [(
            "if uart_remaining = 0 then",
            "if true then",
        )],
        "tests": [directed("f_uart_drop")],
    },
    {
        "id": 13,
        "name": "forwarding priority swapped (MEM/WB beats EX/MEM)",
        "file": "forwarding_unit.vhd",
        "edits": [(
            "      if exv = '1' and exrw = '1' and exrd = reg then\n"
            "        if exld = '1' then\n"
            "          s := FWD_ID;                  -- never forward a load from EX/MEM\n"
            "        else\n"
            "          s := FWD_EXMEM;\n"
            "        end if;\n"
            "      elsif memv = '1' and memrw = '1' and memrd = reg then\n"
            "        s := FWD_MEMWB;\n"
            "      end if;",
            "      if memv = '1' and memrw = '1' and memrd = reg then\n"
            "        s := FWD_MEMWB;\n"
            "      elsif exv = '1' and exrw = '1' and exrd = reg then\n"
            "        if exld = '1' then\n"
            "          s := FWD_ID;\n"
            "        else\n"
            "          s := FWD_EXMEM;\n"
            "        end if;\n"
            "      end if;",
        )],
        "tests": [directed("j_double_producer")],
    },
    {
        "id": 14,
        "name": "forward from EX/MEM when the producer is a load",
        "file": "forwarding_unit.vhd",
        "edits": [(
            "        if exld = '1' then\n"
            "          s := FWD_ID;                  -- never forward a load from EX/MEM\n"
            "        else\n"
            "          s := FWD_EXMEM;\n"
            "        end if;",
            "        s := FWD_EXMEM;",
        )],
        "tests": [leaf("tb_forwarding")],
    },
    {
        "id": 15,
        "name": "R0 producer forwarded",
        "file": "forwarding_unit.vhd",
        "edits": [("    if reg /= \"0000\" then", "    if true then")],
        "tests": [directed("j_r0_producer")],
    },
    {
        "id": 16,
        "name": "forwarding to operand B disabled",
        "file": "forwarding_unit.vhd",
        "edits": [("      sel_b <= b;", "      sel_b <= FWD_ID;")],
        "tests": [directed("j_alu_alu_d1")],
    },
    {
        "id": 17,
        "name": "forwarding to the branch comparator removed",
        "file": "minicpu16_core.vhd",
        "edits": [(
            "      a     => a_ex,\n      b     => b_ex,",
            "      a     => id_ex_rs1_val,\n      b     => id_ex_rs2_val,",
        )],
        "tests": [directed("j_load_branch_d1"), directed("j_alu_branch_d1")],
    },
    {
        "id": 18,
        "name": "forwarding to the JALR base removed",
        "file": "minicpu16_core.vhd",
        "edits": [(
            "  jalr_target   <= std_logic_vector(unsigned(a_ex) + unsigned(id_ex_ctrl.imm));",
            "  jalr_target   <= std_logic_vector(unsigned(id_ex_rs1_val) + unsigned(id_ex_ctrl.imm));",
        )],
        "tests": [directed("j_load_jalr_d1"), directed("i_jalr_rd_rs1")],
    },
    {
        "id": 19,
        "name": "forwarding to store data removed",
        "file": "minicpu16_core.vhd",
        "edits": [("        ex_mem_store_data <= b_ex;",
                   "        ex_mem_store_data <= id_ex_rs2_val;")],
        "tests": [directed("b_load_use_store_data")],
    },
    {
        "id": 20,
        "name": "load-use stall removed for stores",
        "file": "hazard_unit.vhd",
        "edits": [(
            "    if id_valid = '1' and redirect = '0' and halt_ex = '0' then",
            "    if id_valid = '1' and redirect = '0' and halt_ex = '0' and id_is_store = '0' then",
        )],
        "tests": [directed("b_load_use_store_data"), directed("j_load_store_addr_d1")],
    },
    {
        "id": 21,
        "name": "extra one-cycle stall at load-use distance 2",
        "file": "hazard_unit.vhd",
        "edits": [(
            "        if ex_valid = '1' and ex_is_load = '1' and ex_rd /= \"0000\" and (s1_ex = '1' or s2_ex = '1') then\n"
            "          st          := '1';\n"
            "          selected_s1 := s1_ex;\n"
            "          selected_s2 := s2_ex;\n"
            "          selected_load := '1';\n"
            "        end if;",
            "        if ex_valid = '1' and ex_is_load = '1' and ex_rd /= \"0000\" and (s1_ex = '1' or s2_ex = '1') then\n"
            "          st          := '1';\n"
            "          selected_s1 := s1_ex;\n"
            "          selected_s2 := s2_ex;\n"
            "          selected_load := '1';\n"
            "        elsif mem_valid = '1' and mem_is_load = '1' and mem_rd /= \"0000\" and (s1_mem = '1' or s2_mem = '1') then\n"
            "          st          := '1';\n"
            "          selected_s1 := s1_mem;\n"
            "          selected_s2 := s2_mem;\n"
            "          selected_load := '1';\n"
            "        end if;",
        )],
        "tests": [directed("j_load_alu_d2")],
    },
    {
        "id": 22,
        "name": "stall wins over flush (5A configuration)",
        "file": "hazard_unit.vhd",
        "edits": [
            ("    if id_valid = '1' and redirect = '0' and halt_ex = '0' then",
             "    if id_valid = '1' then"),
        ],
        "extra_file_edits": [(
            "minicpu16_core.vhd",
            "        if ex_redirect = '1' then\n"
            "          pc <= unsigned(target_ex);\n"
            "        elsif ex_halt = '1' then\n"
            "          pc <= pc;\n"
            "        elsif stall = '1' then\n"
            "          pc <= pc;",
            "        if stall = '1' then\n"
            "          pc <= pc;\n"
            "        elsif ex_redirect = '1' then\n"
            "          pc <= unsigned(target_ex);\n"
            "        elsif ex_halt = '1' then\n"
            "          pc <= pc;",
        )],
        "config": "nofwd",
        "tests": [directed("c_branch_shadow_store")],
    },
    {
        "id": 23,
        "name": "forwarded LB data is zero-extended instead of sign-extended",
        "file": "dmem.vhd",
        "edits": [(
            "    elsif q_load_type = LOAD_LB and b(7) = '1' then\n      rdata <= x\"FF\" & b;",
            "    elsif q_load_type = LOAD_LB and b(7) = '1' then\n      rdata <= x\"00\" & b;",
        )],
        "tests": [directed("j_forward_load_signext")],
    },
    {
        "id": 24,
        "name": "JAL/JALR link forwarded as PC instead of PC+1",
        "file": "minicpu16_core.vhd",
        "edits": [(
            "  result_ex <= pc_plus_1 when (id_ex_ctrl.is_jal = '1' or id_ex_ctrl.is_jalr = '1') else",
            "  result_ex <= id_ex_pc when (id_ex_ctrl.is_jal = '1' or id_ex_ctrl.is_jalr = '1') else",
        )],
        "tests": [directed("j_jal_link_next"), directed("h_r0")],
    },
    {
        "id": 25,
        "name": "forwarding from a bubble/flushed instruction (valid ignored)",
        "file": "forwarding_unit.vhd",
        "edits": [
            ("      if exv = '1' and exrw = '1' and exrd = reg then",
             "      if exrw = '1' and exrd = reg then"),
            ("      elsif memv = '1' and memrw = '1' and memrd = reg then",
             "      elsif memrw = '1' and memrd = reg then"),
        ],
        "tests": [directed("c_branch_shadow_store")],
    },
    {
        "id": 26,
        "name": "rs1/rs2 index mixed in the dependency compare",
        "file": "hazard_unit.vhd",
        "edits": [(
            "    if id_src2_en = '1' and id_rs2 /= \"0000\" and id_rs2 = ex_rd then",
            "    if id_src2_en = '1' and id_rs2 /= \"0000\" and id_rs1 = ex_rd then",
        )],
        "tests": [directed("b_load_use_alu_b")],
    },
    {
        "id": 27,
        "name": "TIMER sample off by one cycle",
        "file": "minicpu16_core.vhd",
        "edits": [("  timer_value <= std_logic_vector(cycle_count + 1);",
                   "  timer_value <= std_logic_vector(cycle_count + 2);")],
        "tests": [],
    },
    {
        "id": 28,
        "name": "UART busy window off by one cycle",
        "file": "mmio.vhd",
        "edits": [("  uart_busy_s <= '1' when uart_remaining > 0 else '0';",
                   "  uart_busy_s <= '1' when uart_remaining > 1 else '0';")],
        "tests": [],
    },
    {
        "id": 29,
        "name": "GPIO_IN synchroniser one stage short",
        "file": "mmio.vhd",
        "edits": [("      when GPIO_IN_ADDR =>\n        w := gpio_sync2;",
                   "      when GPIO_IN_ADDR =>\n        w := gpio_sync1;")],
        "tests": [directed("k_gpio_schedule")],
    },
    {
        "id": 30,
        "name": "retire cycle column off by one in the testbench port",
        "file": "tb/cpu_tb.vhd",
        "edits": [(
            "        puts(ln, dec(cycle)); puts(ln, \" \");\n        puts(ln, hex4(retire_pc)); puts(ln, \" \");",
            "        puts(ln, dec(cycle - 1)); puts(ln, \" \");\n        puts(ln, hex4(retire_pc)); puts(ln, \" \");",
        )],
        "tests": [directed("j_alu_alu_d1")],
    },
]


def apply_edits(rtl: Path, mut: Mutation) -> None:
    path = rtl / str(mut["file"])
    text = path.read_text(encoding="utf-8")
    for old, new in mut["edits"]:  # type: ignore[misc]
        if old not in text:
            raise RuntimeError("mutation %d: pattern not found in %s:\n%r" %
                               (mut["id"], path.name, old[:100]))
        text = text.replace(old, new, 1)
    path.write_text(text, encoding="utf-8")
    for name, eold, enew in mut.get("extra_file_edits", []):  # type: ignore[misc]
        epath = rtl / name
        etext = epath.read_text(encoding="utf-8")
        if eold not in etext:
            raise RuntimeError("mutation %d: pattern not found in %s" % (mut["id"], name))
        epath.write_text(etext.replace(eold, enew, 1), encoding="utf-8")


def _details(res: Dict[str, object]) -> str:
    if res.get("error"):
        return "simulation/compile error"
    if res.get("ok"):
        return "pass"
    detail = ""
    if res.get("divergence"):
        d = res["divergence"]
        detail = "divergence %s vs %s" % (d.get("golden_disasm"), d.get("rtl_disasm"))
    elif res.get("state_errors"):
        detail = "; ".join(res["state_errors"][:2])
    elif res.get("expected_errors"):
        detail = "; ".join(res["expected_errors"][:2])
    elif res.get("trace_message"):
        detail = str(res["trace_message"]).splitlines()[0]
    return detail or "mismatch"


def run_directed(rtl: Path, work: Path, name: str, sim: str, ghdl: str,
                 check_cycles: bool = False, forwarding: bool = False) -> Tuple[bool, str]:
    asm = ROOT / "tests" / "directed" / (name + ".asm")
    exp = asm.with_suffix(".expected")
    sched = asm.with_suffix(".gpio")
    instr_words = 65536 if name == "g_pc_wrap" else 2048
    try:
        res = rtl_run.run_case(
            [asm], name=name, sim=sim, rtl_dir=rtl, runtime=False,
            workdir=work, max_cycles=5000, instr_words=instr_words,
            gpio_schedule=sched if sched.exists() else None,
            expected=exp if exp.exists() else None, rebuild=False,
            ghdl=ghdl, ignore_cycles=not check_cycles, forwarding=forwarding)
    except Exception as e:  # noqa: BLE001
        return False, "exception: %s" % e
    return bool(res.get("ok")), _details(res)


def run_leaf(rtl: Path, work: Path, entity: str, sim: str = "nvc",
             ghdl: str = "ghdl") -> Tuple[bool, str]:
    try:
        res = rtl_leaf_tests.run_one(
            sim, entity + ".vhd", entity, ghdl, work, True, rtl)
    except Exception as e:  # noqa: BLE001
        return False, "exception: %s" % e
    if res["ok"]:
        return True, "pass"
    return False, "leaf testbench failed (rc=%s)" % res.get("returncode")


def make_corpus(workdir: Path, n: int) -> List[Tuple[str, Path, bool, int]]:
    """A deterministic fuzz + reference + benchmark corpus.

    It deliberately excludes every directed test, so a fuzz-only column really
    answers "would random verification have found this bug?".
    """
    import random_programs
    import rtl_fuzz
    progdir = workdir / "corpus"
    progdir.mkdir(parents=True, exist_ok=True)
    modes = ["hazards", "straight", "loop", "calls", "coverage",
             "mmio_writes", "fwd"]
    cases: List[Tuple[str, Path, bool, int, Optional[Path]]] = []
    for cname, csrc, cuart in rtl_fuzz.CURATED:
        cp = progdir / ("curated_%s.s" % cname)
        cp.write_text(csrc, encoding="utf-8")
        cases.append(("curated_" + cname, cp, True, cuart, None))
    for i in range(n):
        seed = i
        mode = modes[i % len(modes)]
        gp = random_programs.generate(seed, mode)
        p = progdir / ("fuzz_%04d_%s.s" % (seed, mode))
        p.write_text(gp.source, encoding="utf-8")
        cases.append((("fuzz_%04d_%s" % (seed, mode)), p, True, 10, None))
    for name in ("01_straight", "02_loop", "03_calls", "04_hazards", "05_mmio"):
        cases.append(("ref_" + name,
                      ROOT / "tests" / "reference_traces" / (name + ".asm"),
                      False, 10, None))
    for bname in ("fibonacci", "bubble_sort", "crc16", "muldiv", "hello"):
        cases.append(("bench_" + bname, ROOT / "programs" / (bname + ".s"),
                      True, 10, None))
    # Timing-edge programs: they make the UART busy window and the GPIO
    # synchroniser observable to the fuzz-only column without being "directed
    # tests" (they carry no .expected file in the corpus copy).
    edge = progdir / "edge_uart_busy.s"
    edge.write_text((ROOT / "tests" / "directed" / "f_uart_busy_edge.asm").read_text(),
                    encoding="utf-8")
    cases.append(("edge_uart_busy", edge, False, 10, None))
    gpio_prog = progdir / "edge_gpio_schedule.s"
    gpio_prog.write_text((ROOT / "tests" / "directed" / "k_gpio_schedule.asm").read_text(),
                         encoding="utf-8")
    gpio_sched = progdir / "edge_gpio_schedule.gpio"
    gpio_sched.write_text((ROOT / "tests" / "directed" / "k_gpio_schedule.gpio").read_text(),
                          encoding="utf-8")
    cases.append(("edge_gpio_schedule", gpio_prog, False, 10, gpio_sched))
    timer_prog = progdir / "edge_timer_cycle.s"
    timer_prog.write_text((ROOT / "tests" / "directed" / "f_timer_cycle.asm").read_text(),
                          encoding="utf-8")
    cases.append(("edge_timer_cycle", timer_prog, False, 10, None))
    # Put the timing-edge cases first so the small GHDL subset includes them.
    edge_cases = cases[-3:]
    return edge_cases + cases[:-3]


def run_fuzz(rtl: Path, work: Path, sim: str, ghdl: str,
             corpus: Sequence[Tuple[str, Path, bool, int, Optional[Path]]],
             check_cycles: bool = False, forwarding: bool = False,
             gpio_schedule: Optional[Path] = None) -> Tuple[bool, str]:
    for name, path, runtime, uart, case_sched in corpus:
        sched = case_sched if case_sched is not None else gpio_schedule
        try:
            res = rtl_run.run_case(
                [path], name=name, sim=sim, rtl_dir=rtl, runtime=runtime,
                workdir=work, max_cycles=50000, uart_cycles=uart,
                rebuild=False, ghdl=ghdl, ignore_cycles=not check_cycles,
                forwarding=forwarding, gpio_schedule=sched)
        except Exception as e:  # noqa: BLE001
            return False, "%s: exception %s" % (name, e)
        if res.get("error") or not res.get("ok"):
            return False, "%s: %s" % (name, _details(res))
    return True, "pass"


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="MiniCPU-16 RTL mutation check")
    p.add_argument("--only", type=int, default=None)
    p.add_argument("--workdir", default=str(ROOT / "build" / "mutation"))
    p.add_argument("--list", action="store_true")
    p.add_argument("--corpus", type=int, default=36,
                   help="number of generated fuzz programs in the fuzz-only column")
    p.add_argument("--ghdl-corpus", type=int, default=8,
                   help="generated fuzz programs run on GHDL")
    p.add_argument("--no-ghdl", action="store_true")
    p.add_argument("--no-fuzz", action="store_true")
    p.add_argument("--no-directed", action="store_true")
    p.add_argument("--check-cycles", action="store_true")
    p.add_argument("--forwarding", dest="forwarding", action="store_true", default=False)
    p.add_argument("--json", help="write the matrix as JSON")
    ns = p.parse_args(argv)
    if ns.list:
        for m in MUTATIONS:
            print("%2d  %s" % (m["id"], m["name"]))
        return 0
    workroot = Path(ns.workdir)
    workroot.mkdir(parents=True, exist_ok=True)
    ghdl = rtl_run.find_ghdl(None)
    corpus = make_corpus(workroot, ns.corpus)
    ghdl_corpus = corpus[:ns.ghdl_corpus]
    gpio_sched = workroot / "corpus.gpio"
    gpio_sched.write_text("# cycle value\n1 4369\n20 8738\n40 13107\n",
                          encoding="utf-8")
    rows = []
    for mut in MUTATIONS:
        if ns.only is not None and int(mut["id"]) != ns.only:
            continue
        mid = int(mut["id"])
        base = workroot / ("mut_%02d" % mid)
        if base.exists():
            shutil.rmtree(base)
        rtl = base / "rtl"
        shutil.copytree(ROOT / "rtl", rtl)
        apply_edits(rtl, mut)

        row = {"id": mid, "name": mut["name"],
               "directed_nvc": None, "fuzz_nvc": None,
               "nvc": None, "ghdl": None,
               "directed_detail": "", "fuzz_detail": "", "ghdl_detail": ""}

        fwd = ns.forwarding and mut.get("config") != "nofwd"
        directed_tests = list(mut["tests"])  # type: ignore[misc]

        # Directed tests (the per-mutation targeted list) on NVC.
        if not ns.no_directed and directed_tests:
            ok = True
            detail = ""
            for kind, name in directed_tests:
                if kind == "leaf":
                    good, why = run_leaf(rtl, base / "work" / "leaf_nvc" / name,
                                         name, "nvc", ghdl)
                else:
                    good, why = run_directed(rtl, base / "work" / "nvc", name,
                                             "nvc", ghdl, ns.check_cycles, fwd)
                if not good:
                    ok, detail = False, "%s: %s" % (name, why)
                    break
            row["directed_nvc"] = not ok
            row["directed_detail"] = detail

        # Fuzz + references + benchmarks only, on NVC.
        if not ns.no_fuzz:
            ok, why = run_fuzz(rtl, base / "work" / "fuzz_nvc", "nvc", ghdl,
                               corpus, ns.check_cycles, fwd, gpio_sched)
            row["fuzz_nvc"] = not ok
            row["fuzz_detail"] = why

        if row["directed_nvc"] is None and row["fuzz_nvc"] is None:
            row["nvc"] = None
        else:
            row["nvc"] = bool(row["directed_nvc"] or row["fuzz_nvc"])

        # GHDL: targeted directed tests plus a generated fuzz subset.
        if not ns.no_ghdl:
            ok = True
            detail = ""
            for kind, name in directed_tests:
                if kind == "leaf":
                    good, why = run_leaf(rtl, base / "work" / "leaf_ghdl" / name,
                                         name, "ghdl", ghdl)
                else:
                    good, why = run_directed(rtl, base / "work" / "ghdl", name,
                                             "ghdl", ghdl, ns.check_cycles, fwd)
                if not good:
                    ok, detail = False, "%s: %s" % (name, why)
                    break
            if ok:
                good, why = run_fuzz(rtl, base / "work" / "fuzz_ghdl", "ghdl", ghdl,
                                     ghdl_corpus, ns.check_cycles, fwd, gpio_sched)
                if not good:
                    ok, detail = False, why
            row["ghdl"] = not ok
            row["ghdl_detail"] = detail
        rows.append(row)
        print("mutation %2d  directed=%-5s fuzz=%-5s nvc=%-5s ghdl=%-5s  %s" %
              (mid, row["directed_nvc"], row["fuzz_nvc"], row["nvc"], row["ghdl"],
               mut["name"]))

    print()
    print("mutation matrix (True = detected):")
    print("  id  directed  fuzz-only  NVC   GHDL  name")
    n_nvc = 0
    n_fuzz = 0
    n_ghdl = 0
    for row in rows:
        print("  %2d  %-8s  %-9s  %-5s %-5s %s" %
              (row["id"], row["directed_nvc"], row["fuzz_nvc"],
               row["nvc"], row["ghdl"], row["name"]))
        if row["nvc"]:
            n_nvc += 1
        if row["fuzz_nvc"]:
            n_fuzz += 1
        if row["ghdl"]:
            n_ghdl += 1
    total = len(rows)
    print("mutations: %d, detected NVC=%d, GHDL=%d, fuzz-only=%d" %
          (total, n_nvc, n_ghdl, n_fuzz))
    if ns.json:
        Path(ns.json).write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n")
    fuzz_det = sum(1 for r in rows if r["fuzz_nvc"] is True)
    ok_all = n_nvc == total and (ns.no_ghdl or n_ghdl == total)
    print("fuzz-only detected %d/%d (%.1f%%); threshold >=90%%"
          % (fuzz_det, total, 100.0 * fuzz_det / total))
    ok_fuzz = fuzz_det * 10 >= total * 9
    return 0 if (ok_all and ok_fuzz) else 1


if __name__ == "__main__":
    raise SystemExit(main())