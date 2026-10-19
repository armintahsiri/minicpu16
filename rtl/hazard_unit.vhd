-- MiniCPU-16 hazard unit.
--
-- FORWARDING = false (Phase 5A):
--   Stall while a producer of one of the ID instruction's ACTUAL source
--   registers is in EX or MEM and its rd is not R0.  A producer in WB is
--   covered by the register-file write-through.
--
-- FORWARDING = true (Phase 5B, structurally present, NOT verified in 5A):
--   Stall only for load-use at distance 1: a load in EX whose rd matches an ID
--   source.  All other producers are covered by the EX/MEM and MEM/WB paths.
--
-- A redirect or HALT in EX suppresses the stall: the younger ID instruction is
-- squashed and must not hold the machine.

library ieee;
use ieee.std_logic_1164.all;

use work.minicpu16_pkg.all;

entity hazard_unit is
  generic (
    FORWARDING : boolean := false
  );
  port (
    id_valid        : in  std_logic;
    id_src1_en      : in  std_logic;
    id_rs1          : in  std_logic_vector(3 downto 0);
    id_src2_en      : in  std_logic;
    id_rs2          : in  std_logic_vector(3 downto 0);
    id_is_store     : in  std_logic;

    ex_valid        : in  std_logic;
    ex_reg_write    : in  std_logic;
    ex_rd           : in  std_logic_vector(3 downto 0);
    ex_is_load      : in  std_logic;

    mem_valid       : in  std_logic;
    mem_reg_write   : in  std_logic;
    mem_rd          : in  std_logic_vector(3 downto 0);
    mem_is_load     : in  std_logic;

    redirect        : in  std_logic;
    halt_ex         : in  std_logic;

    stall           : out std_logic;
    cause_ex        : out std_logic;
    cause_mem       : out std_logic;
    cause_rs1       : out std_logic;
    cause_rs2       : out std_logic;
    cause_store_data: out std_logic;
    cause_load_use  : out std_logic
  );
end entity hazard_unit;

architecture rtl of hazard_unit is
begin

  process (all)
    variable s1_ex, s2_ex, s1_mem, s2_mem : std_logic;
    variable prod_ex, prod_mem             : std_logic;
    variable selected_s1, selected_s2      : std_logic;
    variable selected_load                 : std_logic;
    variable st                            : std_logic;
  begin
    s1_ex  := '0';
    s2_ex  := '0';
    s1_mem := '0';
    s2_mem := '0';
    st     := '0';

    if id_src1_en = '1' and id_rs1 /= "0000" and id_rs1 = ex_rd then
      s1_ex := '1';
    end if;
    if id_src2_en = '1' and id_rs2 /= "0000" and id_rs2 = ex_rd then
      s2_ex := '1';
    end if;
    if id_src1_en = '1' and id_rs1 /= "0000" and id_rs1 = mem_rd then
      s1_mem := '1';
    end if;
    if id_src2_en = '1' and id_rs2 /= "0000" and id_rs2 = mem_rd then
      s2_mem := '1';
    end if;

    prod_ex  := '0';
    prod_mem := '0';
    if ex_valid = '1' and ex_reg_write = '1' and ex_rd /= "0000" and (s1_ex = '1' or s2_ex = '1') then
      prod_ex := '1';
    end if;
    if mem_valid = '1' and mem_reg_write = '1' and mem_rd /= "0000" and (s1_mem = '1' or s2_mem = '1') then
      prod_mem := '1';
    end if;

    selected_s1   := '0';
    selected_s2   := '0';
    selected_load := '0';

    if id_valid = '1' and redirect = '0' and halt_ex = '0' then
      if FORWARDING then
        -- 5B load-use rule.  A load in MEM is distance 2 and is covered by the
        -- MEM/WB forward; only a load in EX needs the bubble.
        if ex_valid = '1' and ex_is_load = '1' and ex_rd /= "0000" and (s1_ex = '1' or s2_ex = '1') then
          st          := '1';
          selected_s1 := s1_ex;
          selected_s2 := s2_ex;
          selected_load := '1';
        end if;
      else
        -- 5A conservative rule.
        if prod_ex = '1' then
          st          := '1';
          selected_s1 := s1_ex;
          selected_s2 := s2_ex;
          selected_load := ex_is_load;
        elsif prod_mem = '1' then
          st          := '1';
          selected_s1 := s1_mem;
          selected_s2 := s2_mem;
          selected_load := mem_is_load;
        end if;
      end if;
    end if;

    stall            <= st;
    if FORWARDING then
      cause_ex  <= '0';
      cause_mem <= '0';
    else
      cause_ex  <= st and prod_ex;
      cause_mem <= st and (not prod_ex) and prod_mem;
    end if;
    cause_rs1        <= st and selected_s1;
    cause_rs2        <= st and selected_s2;
    cause_store_data <= st and id_is_store and selected_s2;
    cause_load_use   <= st and selected_load;
  end process;

end architecture rtl;
