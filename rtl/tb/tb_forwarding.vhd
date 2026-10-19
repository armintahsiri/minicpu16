-- Self-checking testbench for rtl/forwarding_unit.vhd.
-- Directed producer/source/load combinations plus 100,000 deterministic random
-- cases for both FORWARDING=false and FORWARDING=true.

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use std.env.all;

use work.minicpu16_pkg.all;
use work.tb_util.all;

entity tb_forwarding is
end entity tb_forwarding;

architecture sim of tb_forwarding is

  signal id_rs1, id_rs2 : std_logic_vector(3 downto 0) := (others => '0');
  signal ex_valid, ex_reg_write, ex_is_load : std_logic := '0';
  signal ex_rd : std_logic_vector(3 downto 0) := (others => '0');
  signal mem_valid, mem_reg_write : std_logic := '0';
  signal mem_rd : std_logic_vector(3 downto 0) := (others => '0');
  signal sa_f, sb_f, sa_t, sb_t : std_logic_vector(1 downto 0);

  function ref_sel (
    forwarding : boolean;
    reg : std_logic_vector(3 downto 0);
    ex_valid, ex_we, ex_is_load : std_logic;
    ex_rd : std_logic_vector(3 downto 0);
    mem_valid, mem_we : std_logic;
    mem_rd : std_logic_vector(3 downto 0)) return std_logic_vector is
  begin
    if not forwarding or reg = "0000" then
      return FWD_ID;
    end if;
    if ex_valid = '1' and ex_we = '1' and ex_rd = reg then
      if ex_is_load = '1' then
        return FWD_ID;
      else
        return FWD_EXMEM;
      end if;
    elsif mem_valid = '1' and mem_we = '1' and mem_rd = reg then
      return FWD_MEMWB;
    else
      return FWD_ID;
    end if;
  end function;

begin

  dut_f : entity work.forwarding_unit
    generic map (FORWARDING => false)
    port map (id_rs1 => id_rs1, id_rs2 => id_rs2,
              ex_valid => ex_valid, ex_reg_write => ex_reg_write,
              ex_rd => ex_rd, ex_is_load => ex_is_load,
              mem_valid => mem_valid, mem_reg_write => mem_reg_write,
              mem_rd => mem_rd, sel_a => sa_f, sel_b => sb_f);

  dut_t : entity work.forwarding_unit
    generic map (FORWARDING => true)
    port map (id_rs1 => id_rs1, id_rs2 => id_rs2,
              ex_valid => ex_valid, ex_reg_write => ex_reg_write,
              ex_rd => ex_rd, ex_is_load => ex_is_load,
              mem_valid => mem_valid, mem_reg_write => mem_reg_write,
              mem_rd => mem_rd, sel_a => sa_t, sel_b => sb_t);

  process
    variable checks : integer := 0;
    variable fails  : integer := 0;
    variable seed   : unsigned(31 downto 0) := x"F0F0A5A5";
    variable b      : std_logic_vector(31 downto 0);
  begin
    for r1 in 0 to 7 loop
      for r2 in 0 to 7 loop
        for pr in 0 to 5 loop
          id_rs1 <= std_logic_vector(to_unsigned(r1, 4));
          id_rs2 <= std_logic_vector(to_unsigned(r2, 4));
          ex_valid <= '1' when pr = 1 or pr = 2 or pr = 3 else '0';
          ex_reg_write <= '1';
          ex_rd <= "0010";
          ex_is_load <= '1' when pr = 2 else '0';
          mem_valid <= '1' when pr = 3 or pr = 4 else '0';
          mem_reg_write <= '1';
          mem_rd <= "0011";
          wait for 1 ns;
          check (sa_f = FWD_ID and sb_f = FWD_ID, "5A never forwards", checks, fails);
          check (sa_t = ref_sel(true, id_rs1, ex_valid, ex_reg_write, ex_is_load,
                                ex_rd, mem_valid, mem_reg_write, mem_rd),
                 "directed sel_a", checks, fails);
          check (sb_t = ref_sel(true, id_rs2, ex_valid, ex_reg_write, ex_is_load,
                                ex_rd, mem_valid, mem_reg_write, mem_rd),
                 "directed sel_b", checks, fails);
        end loop;
      end loop;
    end loop;

    for i in 1 to 100000 loop
      seed := resize(seed * 1664525 + 1013904223, 32);
      b := std_logic_vector(seed);
      id_rs1 <= b(3 downto 0);
      id_rs2 <= b(7 downto 4);
      ex_valid <= b(8);
      ex_reg_write <= b(9);
      ex_rd <= b(13 downto 10);
      ex_is_load <= b(14);
      mem_valid <= b(15);
      mem_reg_write <= b(16);
      mem_rd <= b(20 downto 17);
      wait for 1 ns;
      check (sa_f = FWD_ID and sb_f = FWD_ID, "random 5A never forwards", checks, fails);
      check (sa_t = ref_sel(true, id_rs1, ex_valid, ex_reg_write, ex_is_load,
                            ex_rd, mem_valid, mem_reg_write, mem_rd),
             "random sel_a", checks, fails);
      check (sb_t = ref_sel(true, id_rs2, ex_valid, ex_reg_write, ex_is_load,
                            ex_rd, mem_valid, mem_reg_write, mem_rd),
             "random sel_b", checks, fails);
    end loop;

    finish_tb("forwarding_unit", checks, fails);
    wait;
  end process;

end architecture sim;
