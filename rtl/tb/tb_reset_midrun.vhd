-- Mid-run reset testbench (Phase 5B).
--
-- Program (8 instructions, RAM initialised to zero):
--   0 li   r1, 0x0100
--   1 lw   r2, 0(r1)        -- must read 0 in both runs
--   2 li   r3, 0xFF00
--   3 li   r4, 0x0041
--   4 sw   r4, 0(r3)        -- UART write, MEM cycle 8
--   5 li   r2, 0x1234       -- WB cycle 10 (suppressed by reset)
--   6 sw   r2, 0(r1)        -- store, MEM cycle 10 (suppressed by reset)
--   7 halt                  -- EX cycle 10
--
-- Reset is asserted at the falling edge in the middle of cycle 10: the store is
-- in MEM and the UART transmitter is busy (its busy window is 9..18 for N=10).
-- No RAM or MMIO state may change during reset; the program restarts at PC 0
-- with the cycle numbering restarting at 1.  The second run's load of 0x0100
-- must still return 0 (the suppressed store must not have committed), and the
-- second run's store must then commit 0x1234.
--
-- RESET_LEN selects the number of reset clock edges (1 or 3).

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use std.env.all;

use work.minicpu16_pkg.all;
use work.tb_util.all;

entity tb_reset_midrun is
  generic (
    RESET_LEN : integer := 1
  );
end entity tb_reset_midrun;

architecture sim of tb_reset_midrun is

  constant PROGRAM : instr_image_t := (
    0 => x"0B100100",   -- li r1, 0x0100
    1 => x"16210000",   -- lw r2, 0(r1)
    2 => x"0B30FF00",   -- li r3, 0xFF00
    3 => x"0B400041",   -- li r4, 0x0041
    4 => x"18430000",   -- sw r4, 0(r3)
    5 => x"0B201234",   -- li r2, 0x1234
    6 => x"18210000",   -- sw r2, 0(r1)
    7 => x"00000000");  -- halt

  constant DATA_ZERO : byte_image_t := (0 => x"00");
  constant RESET_AT  : integer := 10;

  signal clk           : std_logic := '0';
  signal rst           : std_logic := '1';
  signal uart_tx_byte  : std_logic_vector(7 downto 0);
  signal uart_tx_valid : std_logic;
  signal uart_busy     : std_logic;
  signal gpio_out      : std_logic_vector(15 downto 0);
  signal pc_out        : std_logic_vector(15 downto 0);

  signal retire_valid     : std_logic;
  signal retire_pc        : std_logic_vector(15 downto 0);
  signal retire_instr     : std_logic_vector(31 downto 0);
  signal retire_reg_write : std_logic;
  signal retire_rd        : std_logic_vector(3 downto 0);
  signal retire_value     : std_logic_vector(15 downto 0);
  signal retire_mem_kind  : std_logic_vector(1 downto 0);
  signal retire_mem_addr  : std_logic_vector(15 downto 0);
  signal retire_mem_size  : std_logic_vector(1 downto 0);
  signal retire_mem_data  : std_logic_vector(15 downto 0);
  signal retire_taken     : std_logic;
  signal retire_target    : std_logic_vector(15 downto 0);
  signal retire_halt      : std_logic;

begin

  clk <= not clk after 5 ns;

  dut : entity work.minicpu16_core
    generic map (
      FORWARDING => true,
      INSTR_WORDS => 16,
      RAM_BYTES => 512,
      UART_CYCLES_PER_BYTE => 10,
      COUNTING => false,
      INSTR_INIT => PROGRAM,
      DATA_INIT => DATA_ZERO
    )
    port map (
      clk => clk, rst => rst, gpio_in => x"0000",
      uart_tx_byte => uart_tx_byte, uart_tx_valid => uart_tx_valid,
      uart_busy => uart_busy, gpio_out => gpio_out, pc_out => pc_out,
      retire_valid => retire_valid, retire_pc => retire_pc,
      retire_instr => retire_instr, retire_reg_write => retire_reg_write,
      retire_rd => retire_rd, retire_value => retire_value,
      retire_mem_kind => retire_mem_kind, retire_mem_addr => retire_mem_addr,
      retire_mem_size => retire_mem_size, retire_mem_data => retire_mem_data,
      retire_taken => retire_taken, retire_target => retire_target,
      retire_halt => retire_halt,
      pipe_id_valid => open, pipe_id_pc => open,
      pipe_ex_valid => open, pipe_ex_pc => open,
      pipe_mem_valid => open, pipe_mem_pc => open,
      pipe_wb_valid => open, pipe_wb_pc => open,
      pipe_stall => open, pipe_flush => open,
      cov => open);

  process
    variable checks : integer := 0;
    variable fails  : integer := 0;
    variable cycle  : integer := 1;
    variable nuart  : integer := 0;
    variable last_uart : integer := 0;
    variable stores_before_reset : integer := 0;
    variable load_pc1_value : integer := -1;
    variable store_data : integer := -1;
    variable first_pc_phase2 : integer := -1;
    variable first_cycle_phase2 : integer := -1;
    variable halted : boolean := false;
    variable addr_i : integer;
  begin
    rst <= '1';
    wait until rising_edge(clk);
    wait until rising_edge(clk);
    wait until rising_edge(clk);
    wait until falling_edge(clk);
    rst <= '0';
    cycle := 1;
    wait until rising_edge(clk);
    cycle := 2;

    -- Phase 1: run to cycle RESET_AT, then assert reset mid-cycle.
    loop
      wait until falling_edge(clk);
      exit when cycle >= RESET_AT;
      if uart_tx_valid = '1' then
        nuart := nuart + 1;
        last_uart := to_integer(unsigned(uart_tx_byte));
      end if;
      if retire_valid = '1' then
        if retire_mem_kind = "10" and retire_mem_addr = x"0100" then
          stores_before_reset := stores_before_reset + 1;
        end if;
        if retire_halt = '1' then
          report "phase 1 halted before reset" severity failure;
        end if;
      end if;
      wait until rising_edge(clk);
      cycle := cycle + 1;
    end loop;

    -- Assert reset at this falling edge (middle of cycle RESET_AT).
    rst <= '1';
    for i in 1 to RESET_LEN loop
      wait until rising_edge(clk);
    end loop;
    wait until falling_edge(clk);

    -- Checks while reset is still asserted (after RESET_LEN edges).
    check (pc_out = x"0000", "mid-run reset PC=0", checks, fails);
    check (gpio_out = x"0000", "mid-run reset GPIO_OUT=0", checks, fails);
    check (uart_busy = '0', "mid-run reset UART busy cleared", checks, fails);
    check (uart_tx_valid = '0', "mid-run reset no UART start", checks, fails);
    check (retire_valid = '0', "mid-run reset no retirement", checks, fails);
    check (stores_before_reset = 0, "no RAM store retired before reset", checks, fails);
    check (nuart = 1, "exactly one UART byte before reset", checks, fails);
    check (last_uart = 16#41#, "first UART byte is 0x41", checks, fails);

    -- Release reset: this falling edge is the start of cycle 1 of the second run.
    rst <= '0';
    cycle := 1;
    wait until rising_edge(clk);
    cycle := 2;

    -- Phase 2: the program restarts at PC 0.
    while not halted loop
      wait until falling_edge(clk);
      if uart_tx_valid = '1' then
        nuart := nuart + 1;
        last_uart := to_integer(unsigned(uart_tx_byte));
      end if;
      if retire_valid = '1' then
        if first_pc_phase2 < 0 then
          first_pc_phase2 := to_integer(unsigned(retire_pc));
          first_cycle_phase2 := cycle;
        end if;
        if retire_pc = x"0001" and retire_mem_kind = "01" then
          load_pc1_value := to_integer(unsigned(retire_value));
        end if;
        if retire_mem_kind = "10" and retire_mem_addr = x"0100" then
          store_data := to_integer(unsigned(retire_mem_data));
        end if;
        if retire_halt = '1' then
          halted := true;
        end if;
      end if;
      if cycle > 100 then
        report "phase 2 did not halt" severity failure;
      end if;
      wait until rising_edge(clk);
      cycle := cycle + 1;
    end loop;

    check (first_pc_phase2 = 0, "second run starts at PC 0", checks, fails);
    check (first_cycle_phase2 = 5, "second run cycle numbering restarts at 1",
           checks, fails);
    check (load_pc1_value = 0, "suppressed store did not commit (load=0)", checks, fails);
    check (store_data = 16#1234#, "second run store commits 0x1234", checks, fails);
    check (nuart = 2 and last_uart = 16#41#, "two UART bytes total", checks, fails);
    check (gpio_out = x"0000", "GPIO_OUT stays 0 after reset", checks, fails);

    finish_tb("reset_midrun_len" & integer'image(RESET_LEN), checks, fails);
    wait;
  end process;

end architecture sim;
