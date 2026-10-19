-- Self-checking testbench for rtl/dmem.vhd.
-- Every access type at every alignment, bank selection, boundaries, unmapped
-- addresses and sign/zero extension.

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use std.env.all;

use work.minicpu16_pkg.all;
use work.tb_util.all;

entity tb_dmem is
end entity tb_dmem;

architecture sim of tb_dmem is

  function make_init return byte_image_t is
    variable r : byte_image_t(0 to 63);
  begin
    for i in 0 to 63 loop
      r(i) := std_logic_vector(to_unsigned((i * 3 + 1) mod 256, 8));
    end loop;
    return r;
  end function;

  constant INIT : byte_image_t := make_init;

  signal clk        : std_logic := '0';
  signal rst        : std_logic := '1';
  signal addr       : std_logic_vector(15 downto 0) := (others => '0');
  signal load_en    : std_logic := '0';
  signal load_type  : std_logic_vector(1 downto 0) := LOAD_LBU;
  signal store_en   : std_logic := '0';
  signal store_type : std_logic := '0';
  signal wdata      : std_logic_vector(15 downto 0) := (others => '0');
  signal rdata      : std_logic_vector(15 downto 0);

  function sl (v : integer) return std_logic_vector is
  begin
    return std_logic_vector(to_unsigned(v mod 65536, 16));
  end function;

  function img (v : integer) return string is
  begin
    return integer'image(v);
  end function;

begin

  clk <= not clk after 5 ns;

  dut : entity work.dmem
    generic map (RAM_BYTES => 64, INIT => INIT)
    port map (
      clk => clk, rst => rst, addr => addr,
      load_en => load_en, load_type => load_type,
      store_en => store_en, store_type => store_type,
      wdata => wdata, rdata => rdata);

  process
    variable checks : integer := 0;
    variable fails  : integer := 0;
    variable want   : integer;
  begin
    rst <= '1';
    wait until rising_edge(clk);
    wait until rising_edge(clk);
    wait for 1 ns;
    check (rdata = x"0000", "reset read data", checks, fails);
    rst <= '0';

    -- Initial byte image, LBU and LB.
    for i in 0 to 63 loop
      addr <= sl(i);
      load_en <= '1';
      load_type <= LOAD_LBU;
      wait until rising_edge(clk);
      wait for 1 ns;
      check (rdata = x"00" & INIT(i), "initial LBU " & img(i), checks, fails);
      load_type <= LOAD_LB;
      wait until rising_edge(clk);
      wait for 1 ns;
      want := to_integer(unsigned(INIT(i)));
      if want >= 16#80# then want := want + 16#FF00#; end if;
      check (rdata = sl(want), "initial LB " & img(i), checks, fails);
    end loop;
    load_en <= '0';

    -- Initial word reads at every even address.
    for i in 0 to 31 loop
      addr <= sl(2 * i);
      load_en <= '1';
      load_type <= LOAD_LW;
      wait until rising_edge(clk);
      wait for 1 ns;
      want := to_integer(unsigned(INIT(2 * i))) +
              to_integer(unsigned(INIT(2 * i + 1))) * 256;
      check (rdata = sl(want), "initial LW " & img(2 * i), checks, fails);
    end loop;
    load_en <= '0';

    -- SW then LW at the same and at misaligned addresses.
    addr <= sl(16#10#); store_en <= '1'; store_type <= '1'; wdata <= x"A55A";
    wait until rising_edge(clk);
    wait for 1 ns;
    store_en <= '0';
    for a in 16 to 17 loop
      addr <= sl(a); load_en <= '1'; load_type <= LOAD_LW;
      wait until rising_edge(clk);
      wait for 1 ns;
      check (rdata = x"A55A", "SW/LW at " & img(a), checks, fails);
    end loop;
    load_en <= '0';

    -- SB low/high bank and LB/LBU extension.
    addr <= sl(16#20#); store_en <= '1'; store_type <= '0'; wdata <= x"0080";
    wait until rising_edge(clk);
    wait for 1 ns;
    store_en <= '0';
    addr <= sl(16#21#); store_en <= '1'; store_type <= '0'; wdata <= x"007F";
    wait until rising_edge(clk);
    wait for 1 ns;
    store_en <= '0';
    addr <= sl(16#20#); load_en <= '1'; load_type <= LOAD_LB;
    wait until rising_edge(clk);
    wait for 1 ns;
    check (rdata = x"FF80", "LB sign extension", checks, fails);
    load_type <= LOAD_LBU;
    wait until rising_edge(clk);
    wait for 1 ns;
    check (rdata = x"0080", "LBU zero extension", checks, fails);
    addr <= sl(16#21#); load_type <= LOAD_LB;
    wait until rising_edge(clk);
    wait for 1 ns;
    check (rdata = x"007F", "LB positive", checks, fails);
    load_type <= LOAD_LW;
    wait until rising_edge(clk);
    wait for 1 ns;
    check (rdata = x"7F80", "word from two SB banks", checks, fails);

    -- Misaligned SW writes the aligned word; LW ignores bit 0.
    addr <= sl(16#23#); store_en <= '1'; store_type <= '1'; wdata <= x"1234";
    wait until rising_edge(clk);
    wait for 1 ns;
    store_en <= '0';
    for a in 16#22# to 16#23# loop
      addr <= sl(a); load_en <= '1'; load_type <= LOAD_LW;
      wait until rising_edge(clk);
      wait for 1 ns;
      check (rdata = x"1234", "misaligned SW/LW " & img(a), checks, fails);
    end loop;

    -- Boundary 62/63 is the last word; 64 is unmapped.
    addr <= sl(16#3E#); store_en <= '1'; store_type <= '1'; wdata <= x"CAFE";
    wait until rising_edge(clk);
    wait for 1 ns;
    store_en <= '0';
    addr <= sl(16#3F#); load_en <= '1'; load_type <= LOAD_LW;
    wait until rising_edge(clk);
    wait for 1 ns;
    check (rdata = x"CAFE", "last word via 0x3F", checks, fails);

    addr <= sl(16#40#); store_en <= '1'; store_type <= '1'; wdata <= x"DEAD";
    wait until rising_edge(clk);
    wait for 1 ns;
    store_en <= '0';
    for a in 16#40# to 16#42# loop
      addr <= sl(a); load_en <= '1'; load_type <= LOAD_LW;
      wait until rising_edge(clk);
      wait for 1 ns;
      check (rdata = x"0000", "unmapped read " & img(a), checks, fails);
    end loop;
    addr <= sl(16#FFFE#); load_type <= LOAD_LW;
    wait until rising_edge(clk);
    wait for 1 ns;
    check (rdata = x"0000", "0xFFFE unmapped", checks, fails);
    addr <= x"FFFF"; load_type <= LOAD_LB;
    wait until rising_edge(clk);
    wait for 1 ns;
    check (rdata = x"0000", "0xFFFF unmapped LB", checks, fails);

    -- Same-cycle store/load must not happen in the pipeline; here we check the
    -- read-first bank behaviour is deterministic if a test drives both.
    addr <= sl(16#30#); load_en <= '1'; load_type <= LOAD_LW;
    store_en <= '1'; store_type <= '1'; wdata <= x"BEEF";
    wait until rising_edge(clk);
    wait for 1 ns;
    want := to_integer(unsigned(INIT(48))) + to_integer(unsigned(INIT(49))) * 256;
    check (rdata = sl(want), "same-cycle read returns old value", checks, fails);
    store_en <= '0'; load_en <= '0';
    -- The old value must have been returned (read-first), and the new value
    -- must be visible on the next read.
    addr <= sl(16#30#); load_en <= '1';
    wait until rising_edge(clk);
    wait for 1 ns;
    check (rdata = x"BEEF", "store visible after same-cycle read", checks, fails);

    finish_tb("dmem", checks, fails);
    wait;
  end process;

end architecture sim;
