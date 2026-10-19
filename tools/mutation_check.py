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


def run_directed(rtl: Path, work: Path, name: str) -> Tuple[bool, str]:
    asm = ROOT / "tests" / "directed" / (name + ".asm")
    exp = asm.with_suffix(".expected")
    instr_words = 65536 if name == "g_pc_wrap" else 2048
    try:
        res = rtl_run.run_case(
            [asm], name=name, sim="nvc", rtl_dir=rtl, runtime=False,
            workdir=work, max_cycles=5000, instr_words=instr_words,
            expected=exp if exp.exists() else None, rebuild=False)
    except Exception as e:  # noqa: BLE001
        return False, "exception: %s" % e
    if res.get("error"):
        return False, "simulation/compile error"
    if res.get("ok"):
        return True, "pass"
    detail = ""
    if res.get("divergence"):
        d = res["divergence"]
        detail = "divergence %s vs %s" % (d.get("golden_disasm"), d.get("rtl_disasm"))
    elif res.get("state_errors"):
        detail = "; ".join(res["state_errors"][:2])
    elif res.get("expected_errors"):
        detail = "; ".join(res["expected_errors"][:2])
    return False, detail or "mismatch"


def run_leaf(rtl: Path, work: Path, entity: str) -> Tuple[bool, str]:
    try:
        res = rtl_leaf_tests.run_one(
            "nvc", entity + ".vhd", entity, "ghdl",
            work, True, rtl)
    except Exception as e:  # noqa: BLE001
        return False, "exception: %s" % e
    if res["ok"]:
        return True, "pass"
    return False, "leaf testbench failed (rc=%s)" % res.get("returncode")


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Phase 5A RTL mutation check")
    p.add_argument("--only", type=int, default=None)
    p.add_argument("--workdir", default=str(ROOT / "build" / "mutation"))
    p.add_argument("--list", action="store_true")
    ns = p.parse_args(argv)
    if ns.list:
        for m in MUTATIONS:
            print("%2d  %s" % (m["id"], m["name"]))
        return 0
    workroot = Path(ns.workdir)
    workroot.mkdir(parents=True, exist_ok=True)
    rows = []
    gaps = 0
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
        work = base / "work"
        detected = None
        detail = ""
        for kind, name in mut["tests"]:  # type: ignore[misc]
            if kind == "leaf":
                ok, why = run_leaf(rtl, work / "leaf" / name, name)
            else:
                ok, why = run_directed(rtl, work, name)
            if not ok:
                detected = name
                detail = why
                break
        if detected:
            print("DETECTED  %2d %-62s by %s" % (mid, mut["name"], detected))
            rows.append((mid, mut["name"], detected, detail))
        else:
            gaps += 1
            print("NOT DETECTED %2d %s" % (mid, mut["name"]))
            rows.append((mid, mut["name"], "NOT DETECTED", ""))
    print()
    print("mutation table:")
    for mid, name, test, detail in rows:
        print("  %2d  %-62s  %s" % (mid, name, test))
    print("mutations: %d, not detected: %d" % (len(rows), gaps))
    return 0 if gaps == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())