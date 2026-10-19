-- Self-checking testbench for rtl/hazard_unit.vhd.
-- Directed source/producer combinations plus 100,000 deterministic random
-- cases for both FORWARDING=false and FORWARDING=true.

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use std.env.all;

use work.minicpu16_pkg.all;
use work.tb_util.all;

entity tb_hazard is
end entity tb_hazard;

architecture sim of tb_hazard is

  signal id_valid, id_src1_en, id_src2_en, id_is_store : std_logic := '0';
  signal id_rs1, id_rs2 : std_logic_vector(3 downto 0) := (others => '0');
  signal ex_valid, ex_reg_write, ex_is_load : std_logic := '0';
  signal ex_rd : std_logic_vector(3 downto 0) := (others => '0');
  signal mem_valid, mem_reg_write, mem_is_load : std_logic := '0';
  signal mem_rd : std_logic_vector(3 downto 0) := (others => '0');
  signal redirect, halt_ex : std_logic := '0';

  signal st_f, ce_f, cm_f, cr1_f, cr2_f, csd_f, clu_f : std_logic;
  signal st_t, ce_t, cm_t, cr1_t, cr2_t, csd_t, clu_t : std_logic;

  function expected_mask (
    forwarding : boolean;
    id_valid, id_src1_en, id_src2_en, id_is_store : std_logic;
    id_rs1, id_rs2 : std_logic_vector(3 downto 0);
    ex_valid, ex_reg_write, ex_is_load : std_logic;
    ex_rd : std_logic_vector(3 downto 0);
    mem_valid, mem_reg_write, mem_is_load : std_logic;
    mem_rd : std_logic_vector(3 downto 0);
    redirect, halt_ex : std_logic) return std_logic_vector is
    variable s1e, s2e, s1m, s2m : std_logic;
    variable pe, pm : std_logic;
    variable sel1, sel2, selload : std_logic;
    variable st : std_logic;
    variable r : std_logic_vector(6 downto 0);
  begin
    s1e := '0'; s2e := '0'; s1m := '0'; s2m := '0';
    if id_src1_en = '1' and id_rs1 /= "0000" and id_rs1 = ex_rd then s1e := '1'; end if;
    if id_src2_en = '1' and id_rs2 /= "0000" and id_rs2 = ex_rd then s2e := '1'; end if;
    if id_src1_en = '1' and id_rs1 /= "0000" and id_rs1 = mem_rd then s1m := '1'; end if;
    if id_src2_en = '1' and id_rs2 /= "0000" and id_rs2 = mem_rd then s2m := '1'; end if;
    pe := '0'; pm := '0';
    if ex_valid = '1' and ex_reg_write = '1' and ex_rd /= "0000" and (s1e = '1' or s2e = '1') then pe := '1'; end if;
    if mem_valid = '1' and mem_reg_write = '1' and mem_rd /= "0000" and (s1m = '1' or s2m = '1') then pm := '1'; end if;

    st := '0'; sel1 := '0'; sel2 := '0'; selload := '0';
    if id_valid = '1' and redirect = '0' and halt_ex = '0' then
      if forwarding then
        if ex_valid = '1' and ex_is_load = '1' and ex_rd /= "0000" and (s1e = '1' or s2e = '1') then
          st := '1'; sel1 := s1e; sel2 := s2e; selload := '1';
        end if;
      else
        if pe = '1' then
          st := '1'; sel1 := s1e; sel2 := s2e; selload := ex_is_load;
        elsif pm = '1' then
          st := '1'; sel1 := s1m; sel2 := s2m; selload := mem_is_load;
        end if;
      end if;
    end if;

    r(0) := st;
    if forwarding then
      r(1) := '0'; r(2) := '0';
    else
      r(1) := st and pe;
      r(2) := st and (not pe) and pm;
    end if;
    r(3) := st and sel1;
    r(4) := st and sel2;
    r(5) := st and id_is_store and sel2;
    r(6) := st and selload;
    return r;
  end function;

  function actual_mask (st, ce, cm, cr1, cr2, csd, clu : std_logic) return std_logic_vector is
    variable r : std_logic_vector(6 downto 0);
  begin
    r(0) := st; r(1) := ce; r(2) := cm; r(3) := cr1;
    r(4) := cr2; r(5) := csd; r(6) := clu;
    return r;
  end function;

begin

  dut_f : entity work.hazard_unit
    generic map (FORWARDING => false)
    port map (
      id_valid => id_valid, id_src1_en => id_src1_en, id_rs1 => id_rs1,
      id_src2_en => id_src2_en, id_rs2 => id_rs2, id_is_store => id_is_store,
      ex_valid => ex_valid, ex_reg_write => ex_reg_write, ex_rd => ex_rd,
      ex_is_load => ex_is_load,
      mem_valid => mem_valid, mem_reg_write => mem_reg_write, mem_rd => mem_rd,
      mem_is_load => mem_is_load,
      redirect => redirect, halt_ex => halt_ex,
      stall => st_f, cause_ex => ce_f, cause_mem => cm_f,
      cause_rs1 => cr1_f, cause_rs2 => cr2_f,
      cause_store_data => csd_f, cause_load_use => clu_f);

  dut_t : entity work.hazard_unit
    generic map (FORWARDING => true)
    port map (
      id_valid => id_valid, id_src1_en => id_src1_en, id_rs1 => id_rs1,
      id_src2_en => id_src2_en, id_rs2 => id_rs2, id_is_store => id_is_store,
      ex_valid => ex_valid, ex_reg_write => ex_reg_write, ex_rd => ex_rd,
      ex_is_load => ex_is_load,
      mem_valid => mem_valid, mem_reg_write => mem_reg_write, mem_rd => mem_rd,
      mem_is_load => mem_is_load,
      redirect => redirect, halt_ex => halt_ex,
      stall => st_t, cause_ex => ce_t, cause_mem => cm_t,
      cause_rs1 => cr1_t, cause_rs2 => cr2_t,
      cause_store_data => csd_t, cause_load_use => clu_t);

  process
    variable checks : integer := 0;
    variable fails  : integer := 0;
    variable seed   : unsigned(31 downto 0) := x"00A55A55";
    variable b      : std_logic_vector(31 downto 0);
    variable want_f, want_t : std_logic_vector(6 downto 0);
    variable got_f, got_t   : std_logic_vector(6 downto 0);
  begin
    -- Directed cases: all producer/consumer/source combinations with 4 regs.
    for src1 in 0 to 3 loop
      for src2 in 0 to 3 loop
        for pr in 0 to 5 loop
          id_valid <= '1';
          id_src1_en <= '1';
          id_src2_en <= '1';
          id_rs1 <= std_logic_vector(to_unsigned(src1, 4));
          id_rs2 <= std_logic_vector(to_unsigned(src2, 4));
          id_is_store <= '1' when (pr mod 2) = 0 else '0';
          ex_valid <= '1' when pr = 1 or pr = 2 or pr = 3 else '0';
          ex_reg_write <= '1';
          ex_rd <= "0010";
          ex_is_load <= '1' when pr = 2 else '0';
          mem_valid <= '1' when pr = 3 or pr = 4 else '0';
          mem_reg_write <= '1';
          mem_rd <= "0011";
          mem_is_load <= '1' when pr = 4 else '0';
          redirect <= '0';
          halt_ex <= '0';
          wait for 1 ns;
          want_f := expected_mask(false, id_valid, id_src1_en, id_src2_en, id_is_store,
                                  id_rs1, id_rs2, ex_valid, ex_reg_write, ex_is_load,
                                  ex_rd, mem_valid, mem_reg_write, mem_is_load, mem_rd,
                                  redirect, halt_ex);
          want_t := expected_mask(true, id_valid, id_src1_en, id_src2_en, id_is_store,
                                  id_rs1, id_rs2, ex_valid, ex_reg_write, ex_is_load,
                                  ex_rd, mem_valid, mem_reg_write, mem_is_load, mem_rd,
                                  redirect, halt_ex);
          got_f := actual_mask(st_f, ce_f, cm_f, cr1_f, cr2_f, csd_f, clu_f);
          got_t := actual_mask(st_t, ce_t, cm_t, cr1_t, cr2_t, csd_t, clu_t);
          check (got_f = want_f, "directed 5A case", checks, fails);
          check (got_t = want_t, "directed 5B case", checks, fails);
        end loop;
      end loop;
    end loop;

    -- Redirect/HALT suppression.
    id_valid <= '1'; id_src1_en <= '1'; id_src2_en <= '0';
    id_rs1 <= "0010"; id_rs2 <= "0000";
    ex_valid <= '1'; ex_reg_write <= '1'; ex_rd <= "0010"; ex_is_load <= '0';
    mem_valid <= '0'; mem_reg_write <= '0'; mem_rd <= "0000"; mem_is_load <= '0';
    redirect <= '1'; halt_ex <= '0'; wait for 1 ns;
    check (st_f = '0' and st_t = '0', "redirect suppresses stall", checks, fails);
    redirect <= '0'; halt_ex <= '1'; wait for 1 ns;
    check (st_f = '0' and st_t = '0', "halt suppresses stall", checks, fails);
    halt_ex <= '0';

    -- Random cases.
    for i in 1 to 100000 loop
      seed := resize(seed * 1664525 + 1013904223, 32);
      b := std_logic_vector(seed);
      id_valid <= b(0); id_src1_en <= b(1);
      id_rs1 <= b(5 downto 2);
      id_src2_en <= b(6);
      id_rs2 <= b(10 downto 7);
      id_is_store <= b(11);
      ex_valid <= b(12); ex_reg_write <= b(13); ex_rd <= b(17 downto 14);
      ex_is_load <= b(18);
      mem_valid <= b(19); mem_reg_write <= b(20); mem_rd <= b(24 downto 21);
      mem_is_load <= b(25);
      redirect <= b(26); halt_ex <= b(27);
      wait for 1 ns;
      want_f := expected_mask(false, id_valid, id_src1_en, id_src2_en, id_is_store,
                              id_rs1, id_rs2, ex_valid, ex_reg_write, ex_is_load,
                              ex_rd, mem_valid, mem_reg_write, mem_is_load, mem_rd,
                              redirect, halt_ex);
      want_t := expected_mask(true, id_valid, id_src1_en, id_src2_en, id_is_store,
                              id_rs1, id_rs2, ex_valid, ex_reg_write, ex_is_load,
                              ex_rd, mem_valid, mem_reg_write, mem_is_load, mem_rd,
                              redirect, halt_ex);
      got_f := actual_mask(st_f, ce_f, cm_f, cr1_f, cr2_f, csd_f, clu_f);
      got_t := actual_mask(st_t, ce_t, cm_t, cr1_t, cr2_t, csd_t, clu_t);
      check (got_f = want_f, "random 5A case", checks, fails);
      check (got_t = want_t, "random 5B case", checks, fails);
    end loop;

    finish_tb("hazard_unit", checks, fails);
    wait;
  end process;

end architecture sim;
