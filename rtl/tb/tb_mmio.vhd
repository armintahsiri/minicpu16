-- Self-checking testbench for rtl/mmio.vhd.
-- Every register and every byte-access combination, UART busy/drop timing,
-- GPIO_IN synchroniser, TIMER read, reserved addresses and read-only writes.

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use std.env.all;

use work.minicpu16_pkg.all;
use work.tb_util.all;

entity tb_mmio is
end entity tb_mmio;

architecture sim of tb_mmio is

  signal clk             : std_logic := '0';
  signal rst             : std_logic := '1';
  signal addr            : std_logic_vector(15 downto 0) := (others => '0');
  signal load_en         : std_logic := '0';
  signal load_type       : std_logic_vector(1 downto 0) := LOAD_LW;
  signal store_en        : std_logic := '0';
  signal store_type      : std_logic := '0';
  signal wdata           : std_logic_vector(15 downto 0) := (others => '0');
  signal rdata           : std_logic_vector(15 downto 0);
  signal gpio_in         : std_logic_vector(15 downto 0) := (others => '0');
  signal gpio_out        : std_logic_vector(15 downto 0);
  signal timer_value     : std_logic_vector(15 downto 0) := x"ABCD";
  signal uart_tx_byte    : std_logic_vector(7 downto 0);
  signal uart_tx_valid   : std_logic;
  signal uart_busy       : std_logic;
  signal uart_drop_pulse : std_logic;
  signal read_count      : std_logic_vector(31 downto 0);
  signal write_count     : std_logic_vector(31 downto 0);
  signal drop_count      : std_logic_vector(31 downto 0);

  function sl (v : integer) return std_logic_vector is
  begin
    return std_logic_vector(to_unsigned(v mod 65536, 16));
  end function;

  function img (v : integer) return string is
  begin
    return integer'image(v);
  end function;

  procedure do_store (signal a : out std_logic_vector(15 downto 0);
                      signal se : out std_logic;
                      signal st : out std_logic;
                      signal wd : out std_logic_vector(15 downto 0);
                      signal clk : in std_logic;
                      constant av : integer;
                      constant word : boolean;
                      constant dv : integer) is
  begin
    a  <= sl(av);
    se <= '1';
    if word then st <= '1'; else st <= '0'; end if;
    wd <= sl(dv);
    wait until rising_edge(clk);
    wait for 1 ns;
    se <= '0';
  end procedure;

  procedure do_load (signal a : out std_logic_vector(15 downto 0);
                     signal le : out std_logic;
                     signal lt : out std_logic_vector(1 downto 0);
                     signal clk : in std_logic;
                     constant av : integer;
                     constant ltype : std_logic_vector(1 downto 0)) is
  begin
    a  <= sl(av);
    le <= '1';
    lt <= ltype;
    wait until rising_edge(clk);
    wait for 1 ns;
    le <= '0';
  end procedure;

begin

  clk <= not clk after 5 ns;

  dut : entity work.mmio
    generic map (UART_CYCLES_PER_BYTE => 3)
    port map (
      clk => clk, rst => rst, addr => addr,
      load_en => load_en, load_type => load_type,
      store_en => store_en, store_type => store_type,
      wdata => wdata, rdata => rdata,
      gpio_in => gpio_in, gpio_out => gpio_out,
      timer_value => timer_value,
      uart_tx_byte => uart_tx_byte,
      uart_tx_valid => uart_tx_valid,
      uart_busy => uart_busy,
      uart_drop_pulse => uart_drop_pulse,
      read_count => read_count,
      write_count => write_count,
      drop_count => drop_count);

  process
    variable checks : integer := 0;
    variable fails  : integer := 0;
  begin
    rst <= '1';
    wait until rising_edge(clk);
    wait until rising_edge(clk);
    wait for 1 ns;
    check (gpio_out = x"0000", "reset GPIO_OUT", checks, fails);
    check (uart_busy = '0', "reset UART busy", checks, fails);
    check (uart_tx_valid = '0', "reset UART valid", checks, fails);
    rst <= '0';

    -- GPIO_OUT word and byte accesses.
    do_store(addr, store_en, store_type, wdata, clk, 16#FF04#, true, 16#1234#);
    check (gpio_out = x"1234", "GPIO_OUT word write", checks, fails);
    do_load(addr, load_en, load_type, clk, 16#FF04#, LOAD_LW);
    check (rdata = x"1234", "GPIO_OUT word read", checks, fails);
    do_load(addr, load_en, load_type, clk, 16#FF05#, LOAD_LBU);
    check (rdata = x"0034", "GPIO_OUT byte read low", checks, fails);
    do_store(addr, store_en, store_type, wdata, clk, 16#FF05#, false, 16#00AB#);
    check (gpio_out = x"12AB", "GPIO_OUT byte write preserves high", checks, fails);
    do_load(addr, load_en, load_type, clk, 16#FF04#, LOAD_LW);
    check (rdata = x"12AB", "GPIO_OUT after byte write", checks, fails);

    -- UART_TX word write.  Busy is combinational from the countdown, so it is
    -- high in the accepting write's cycle and for exactly N further cycles.
    do_store(addr, store_en, store_type, wdata, clk, 16#FF00#, true, 16#0041#);
    check (uart_tx_valid = '1', "UART TX valid pulse", checks, fails);
    check (uart_tx_byte = x"41", "UART TX byte", checks, fails);
    check (uart_busy = '1', "UART busy after start", checks, fails);

    -- Status while busy, word and LB.  The write commits at the edge ending
    -- cycle c; the countdown still reads N after that edge, N-1 after the next,
    -- and reaches 0 on the N-th following edge.  These two loads sample the
    -- countdown values N-1 and N-2, both of which must read busy (contract
    -- section 7.2, finding F25).
    do_load(addr, load_en, load_type, clk, 16#FF02#, LOAD_LW);
    check (rdata = x"0001", "UART_STATUS busy word", checks, fails);
    do_load(addr, load_en, load_type, clk, 16#FF02#, LOAD_LB);
    check (rdata = x"0001", "UART_STATUS busy LB", checks, fails);

    -- One more edge brings the countdown to N-3 = 0, so a write in that cycle
    -- is still dropped (the countdown is N-3 > 0 before the edge).
    do_store(addr, store_en, store_type, wdata, clk, 16#FF00#, true, 16#0042#);
    check (uart_tx_valid = '0', "UART write while busy is dropped", checks, fails);
    check (uart_drop_pulse = '1', "UART drop pulse", checks, fails);
    check (drop_count = x"00000001", "UART drop count", checks, fails);

    check (uart_busy = '0', "UART busy clears", checks, fails);
    do_load(addr, load_en, load_type, clk, 16#FF00#, LOAD_LW);
    check (rdata = x"0000", "UART_TX read returns 0", checks, fails);
    do_load(addr, load_en, load_type, clk, 16#FF02#, LOAD_LW);
    check (rdata = x"0000", "UART_STATUS idle", checks, fails);

    -- Another byte accepts, including SB at the odd address 0xFF01.
    do_store(addr, store_en, store_type, wdata, clk, 16#FF01#, false, 16#0044#);
    check (uart_tx_valid = '1', "UART odd-address SB transmits", checks, fails);
    check (uart_tx_byte = x"44", "UART odd-address byte", checks, fails);
    wait until rising_edge(clk);
    wait until rising_edge(clk);
    wait until rising_edge(clk);
    wait until rising_edge(clk);

    -- GPIO_IN synchroniser: two edges after driving the pins.
    gpio_in <= x"55AA";
    wait until rising_edge(clk);
    wait until rising_edge(clk);
    wait for 1 ns;
    do_load(addr, load_en, load_type, clk, 16#FF06#, LOAD_LW);
    check (rdata = x"55AA", "GPIO_IN synchronised word", checks, fails);
    do_load(addr, load_en, load_type, clk, 16#FF07#, LOAD_LB);
    check (rdata = x"FFAA", "GPIO_IN LB sign extension", checks, fails);
    do_load(addr, load_en, load_type, clk, 16#FF07#, LOAD_LBU);
    check (rdata = x"00AA", "GPIO_IN LBU zero extension", checks, fails);

    -- TIMER reads: word, LB sign-extension, LBU.
    do_load(addr, load_en, load_type, clk, 16#FF08#, LOAD_LW);
    check (rdata = x"ABCD", "TIMER word", checks, fails);
    do_load(addr, load_en, load_type, clk, 16#FF09#, LOAD_LB);
    check (rdata = x"FFCD", "TIMER LB", checks, fails);
    do_load(addr, load_en, load_type, clk, 16#FF09#, LOAD_LBU);
    check (rdata = x"00CD", "TIMER LBU", checks, fails);

    -- Writes to read-only registers are ignored.
    do_store(addr, store_en, store_type, wdata, clk, 16#FF02#, true, 16#FFFF#);
    do_store(addr, store_en, store_type, wdata, clk, 16#FF06#, true, 16#FFFF#);
    do_store(addr, store_en, store_type, wdata, clk, 16#FF08#, true, 16#FFFF#);
    do_store(addr, store_en, store_type, wdata, clk, 16#FF0A#, true, 16#FFFF#);
    check (gpio_out = x"12AB", "read-only writes did not change GPIO_OUT", checks, fails);
    do_load(addr, load_en, load_type, clk, 16#FF08#, LOAD_LW);
    check (rdata = x"ABCD", "TIMER write ignored", checks, fails);

    -- Reserved addresses read zero.
    do_load(addr, load_en, load_type, clk, 16#FF0A#, LOAD_LW);
    check (rdata = x"0000", "reserved 0xFF0A", checks, fails);
    do_load(addr, load_en, load_type, clk, 16#FFFF#, LOAD_LW);
    check (rdata = x"0000", "reserved 0xFFFF", checks, fails);

    check (read_count /= x"00000000", "read counter non-zero", checks, fails);
    check (write_count /= x"00000000", "write counter non-zero", checks, fails);
    check (drop_count = x"00000001", "exactly one dropped byte", checks, fails);

    finish_tb("mmio", checks, fails);
    wait;
  end process;

end architecture sim;