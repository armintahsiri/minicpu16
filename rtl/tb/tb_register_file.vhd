-- Self-checking testbench for rtl/register_file.vhd.
-- Directed reset/R0/write-through tests plus 5,000 deterministic random
-- read/write operations against a behavioural shadow array.

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use std.env.all;

use work.minicpu16_pkg.all;
use work.tb_util.all;

entity tb_register_file is
end entity tb_register_file;

architecture sim of tb_register_file is

  signal clk    : std_logic := '0';
  signal rst    : std_logic := '1';
  signal raddr1 : std_logic_vector(3 downto 0) := (others => '0');
  signal raddr2 : std_logic_vector(3 downto 0) := (others => '0');
  signal rdata1 : std_logic_vector(15 downto 0);
  signal rdata2 : std_logic_vector(15 downto 0);
  signal we     : std_logic := '0';
  signal waddr  : std_logic_vector(3 downto 0) := (others => '0');
  signal wdata  : std_logic_vector(15 downto 0) := (others => '0');

  type shadow_t is array (0 to 15) of integer;

  function to_sl (v : integer) return std_logic_vector is
  begin
    return std_logic_vector(to_unsigned(v mod 65536, 16));
  end function;

  function img (v : integer) return string is
  begin
    return integer'image(v);
  end function;

begin

  clk <= not clk after 5 ns;

  dut : entity work.register_file
    port map (
      clk => clk, rst => rst,
      raddr1 => raddr1, raddr2 => raddr2,
      rdata1 => rdata1, rdata2 => rdata2,
      we => we, waddr => waddr, wdata => wdata);

  process
    variable checks : integer := 0;
    variable fails  : integer := 0;
    variable shadow : shadow_t := (others => 0);
    variable seed   : unsigned(31 downto 0) := x"00C0FFEE";
    variable a, b   : integer;
    variable v      : integer;
    variable do_wr  : boolean;
  begin
    -- Reset for two edges.
    rst <= '1';
    we <= '0';
    wait until rising_edge(clk);
    wait until rising_edge(clk);
    wait for 1 ns;
    rst <= '0';

    -- Write all registers 1..15 and read both ports.
    for i in 1 to 15 loop
      we <= '1';
      waddr <= std_logic_vector(to_unsigned(i, 4));
      wdata <= to_sl(16#1000# + i);
      wait until rising_edge(clk);
      wait for 1 ns;
      shadow(i) := 16#1000# + i;
      we <= '0';
      raddr1 <= std_logic_vector(to_unsigned(i, 4));
      raddr2 <= std_logic_vector(to_unsigned(i, 4));
      wait for 1 ns;
      check (rdata1 = to_sl(shadow(i)), "readback r" & img(i), checks, fails);
      check (rdata2 = to_sl(shadow(i)), "readback port2 r" & img(i), checks, fails);
    end loop;

    -- R0 write must be discarded.
    we <= '1';
    waddr <= "0000";
    wdata <= x"FFFF";
    wait until rising_edge(clk);
    wait for 1 ns;
    we <= '0';
    raddr1 <= "0000";
    raddr2 <= "0000";
    wait for 1 ns;
    check (rdata1 = x"0000", "R0 reads zero after write", checks, fails);
    check (rdata2 = x"0000", "R0 reads zero after write port2", checks, fails);

    -- Write-through: ID must see the WB value in the same cycle, before the
    -- rising edge that commits it.
    raddr1 <= "0101";
    raddr2 <= "0101";
    we <= '1';
    waddr <= "0101";
    wdata <= x"BEEF";
    wait for 1 ns;
    check (rdata1 = x"BEEF", "write-through port1", checks, fails);
    check (rdata2 = x"BEEF", "write-through port2", checks, fails);
    wait until rising_edge(clk);
    wait for 1 ns;
    shadow(5) := 16#BEEF#;
    we <= '0';
    check (rdata1 = x"BEEF", "write-through after commit", checks, fails);

    -- Reset must not write and must not clear.
    rst <= '1';
    we <= '1';
    waddr <= "0101";
    wdata <= x"DEAD";
    wait until rising_edge(clk);
    wait for 1 ns;
    rst <= '0';
    we <= '0';
    wait for 1 ns;
    check (rdata1 = to_sl(shadow(5)), "reset gated write", checks, fails);
    check (shadow(1) = 16#1001#, "model sanity", checks, fails);

    -- Random read/write operations against the shadow.
    for i in 1 to 5000 loop
      seed := resize(seed * 1664525 + 1013904223, 32);
      a := to_integer(seed(3 downto 0));
      b := to_integer(seed(7 downto 4));
      v := to_integer(seed(31 downto 16));
      do_wr := (seed(15) = '1');
      if do_wr then
        we <= '1';
        waddr <= std_logic_vector(to_unsigned(a, 4));
        wdata <= to_sl(v);
        wait until rising_edge(clk);
        wait for 1 ns;
        if a /= 0 then shadow(a) := v; end if;
        we <= '0';
      end if;
      raddr1 <= std_logic_vector(to_unsigned(b, 4));
      raddr2 <= std_logic_vector(to_unsigned(a, 4));
      wait for 1 ns;
      check (rdata1 = to_sl(shadow(b)), "random read r" & img(b) & " op=" & img(i), checks, fails);
      check (rdata2 = to_sl(shadow(a)), "random read2 r" & img(a) & " op=" & img(i), checks, fails);
    end loop;

    finish_tb("register_file", checks, fails);
    wait;
  end process;

end architecture sim;
