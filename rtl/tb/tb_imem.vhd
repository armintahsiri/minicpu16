-- Self-checking testbench for rtl/imem.vhd: synchronous read latency, all
-- in-range words, and HALT for addresses >= INSTR_WORDS (including 0xFFFF).

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use std.env.all;

use work.minicpu16_pkg.all;
use work.tb_util.all;

entity tb_imem is
end entity tb_imem;

architecture sim of tb_imem is

  constant IMAGE : instr_image_t := (
    0  => x"00000000",
    1  => x"01000000",
    2  => x"0B101234",
    3  => x"0B201234",
    4  => x"0B301234",
    5  => x"0B401234",
    6  => x"0B501234",
    7  => x"0B601234",
    8  => x"0B701234",
    9  => x"0B801234",
    10 => x"0B901234",
    11 => x"0BA01234",
    12 => x"0BB01234",
    13 => x"0BC01234",
    14 => x"0BD01234",
    15 => x"0BE01234");

  signal clk   : std_logic := '0';
  signal rst   : std_logic := '1';
  signal addr  : std_logic_vector(15 downto 0) := (others => '0');
  signal instr : std_logic_vector(31 downto 0);

  function img (v : integer) return string is
  begin
    return integer'image(v);
  end function;

begin

  clk <= not clk after 5 ns;

  dut : entity work.imem
    generic map (INSTR_WORDS => 16, INIT => IMAGE)
    port map (clk => clk, rst => rst, addr => addr, instr => instr);

  process
    variable checks : integer := 0;
    variable fails  : integer := 0;
  begin
    rst <= '1';
    wait until rising_edge(clk);
    wait until rising_edge(clk);
    wait for 1 ns;
    check (instr = INSTR_HALT, "reset output is HALT", checks, fails);
    rst <= '0';

    for i in 0 to 15 loop
      addr <= std_logic_vector(to_unsigned(i, 16));
      wait until rising_edge(clk);
      wait for 1 ns;
      check (instr = IMAGE(i), "in-range word " & img(i), checks, fails);
    end loop;

    for i in 16 to 20 loop
      addr <= std_logic_vector(to_unsigned(i, 16));
      wait until rising_edge(clk);
      wait for 1 ns;
      check (instr = INSTR_HALT, "out-of-range word " & img(i), checks, fails);
    end loop;

    addr <= x"FFFF";
    wait until rising_edge(clk);
    wait for 1 ns;
    check (instr = INSTR_HALT, "0xFFFF is HALT", checks, fails);

    -- The output must not change before the next rising edge.
    addr <= x"0003";
    wait for 1 ns;
    check (instr = INSTR_HALT, "read latency: old data before edge", checks, fails);
    wait until rising_edge(clk);
    wait for 1 ns;
    check (instr = IMAGE(3), "read latency: new data after edge", checks, fails);

    finish_tb("imem", checks, fails);
    wait;
  end process;

end architecture sim;
