-- Reset behaviour testbench.
--
-- Phase 1 runs a small program to HALT (r1=1, RAM[0x100]=1, GPIO_OUT=0x1234,
-- one UART byte 0x34).  Reset is then asserted while the CPU is halted.
-- During reset the TB checks PC=0, GPIO_OUT=0, UART idle and no architectural
-- writes.  After release the program restarts, sees r7=1, and halts immediately:
-- registers and RAM must still hold their warm values, GPIO_OUT and the UART
-- must have been reset, and no second UART byte may appear.

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use std.env.all;

use work.minicpu16_pkg.all;
use work.tb_util.all;

entity tb_reset is
end entity tb_reset;

architecture sim of tb_reset is

  constant PROGRAM : instr_image_t := (
    0  => x"19070001",   -- beq r7, r0, 2
    1  => x"00000000",   -- halt
    2  => x"0B110001",   -- addi r1, r1, 1
    3  => x"0B300100",   -- li r3, 0x0100
    4  => x"18130000",   -- sw r1, 0(r3)
    5  => x"0B40FF04",   -- li r4, GPIO_OUT
    6  => x"0B501234",   -- li r5, 0x1234
    7  => x"18540000",   -- sw r5, 0(r4)
    8  => x"0B60FF00",   -- li r6, UART_TX
    9  => x"18560000",   -- sw r5, 0(r6)
    10 => x"0B700001",   -- li r7, 1
    11 => x"00000000");  -- halt

  constant DATA_ZERO : byte_image_t := (0 => x"00");

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

  type reg_shadow_t is array (0 to 15) of integer;
  type ram_shadow_t is array (0 to 511) of integer;

begin

  clk <= not clk after 5 ns;

  dut : entity work.minicpu16_core
    generic map (
      FORWARDING => false,
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
      retire_halt => retire_halt, cov => open);

  process
    variable checks : integer := 0;
    variable fails  : integer := 0;
    variable regs : reg_shadow_t := (others => 0);
    variable ram  : ram_shadow_t := (others => 0);
    variable nuart : integer := 0;
    variable last_uart : integer := 0;
    variable halted : boolean := false;
    variable addr_i : integer;
    variable cycles : integer := 0;
    variable pc_after_halt : integer := 0;
  begin
    rst <= '1';
    wait until rising_edge(clk);
    wait until rising_edge(clk);
    wait until rising_edge(clk);
    wait until falling_edge(clk);
    rst <= '0';

    -- Phase 1: run to HALT.
    while not halted loop
      wait until falling_edge(clk);
      cycles := cycles + 1;
      if uart_tx_valid = '1' then
        nuart := nuart + 1;
        last_uart := to_integer(unsigned(uart_tx_byte));
      end if;
      if retire_valid = '1' then
        if retire_reg_write = '1' and retire_rd /= "0000" then
          regs(to_integer(unsigned(retire_rd))) := to_integer(unsigned(retire_value));
        end if;
        if retire_mem_kind = "10" then
          addr_i := to_integer(unsigned(retire_mem_addr));
          if retire_mem_size = "01" then
            if addr_i < 512 then ram(addr_i) := to_integer(unsigned(retire_mem_data(7 downto 0))); end if;
          else
            if addr_i < 512 then ram(addr_i) := to_integer(unsigned(retire_mem_data(7 downto 0))); end if;
            if addr_i + 1 < 512 then ram(addr_i + 1) := to_integer(unsigned(retire_mem_data(15 downto 8))); end if;
          end if;
        end if;
        if retire_halt = '1' then
          halted := true;
        end if;
      end if;
      if cycles > 200 then
        report "phase 1 did not halt" severity failure;
      end if;
    end loop;

    check (regs(1) = 1, "phase1 r1=1", checks, fails);
    check (ram(16#100#) = 1, "phase1 RAM[0x100]=1", checks, fails);
    check (gpio_out = x"1234", "phase1 GPIO_OUT", checks, fails);
    check (nuart = 1 and last_uart = 16#34#, "phase1 one UART byte 0x34", checks, fails);
    pc_after_halt := to_integer(unsigned(pc_out));
    wait until rising_edge(clk);
    wait until falling_edge(clk);
    check (to_integer(unsigned(pc_out)) = pc_after_halt, "phase1 PC frozen", checks, fails);
    wait until rising_edge(clk);
    wait until falling_edge(clk);
    check (to_integer(unsigned(pc_out)) = pc_after_halt, "phase1 PC stays frozen", checks, fails);

    -- Assert reset while halted.
    rst <= '1';
    wait until rising_edge(clk);
    wait for 1 ns;
    check (pc_out = x"0000", "reset PC=0", checks, fails);
    check (gpio_out = x"0000", "reset GPIO_OUT=0", checks, fails);
    check (uart_busy = '0', "reset UART busy=0", checks, fails);
    check (uart_tx_valid = '0', "reset no UART start", checks, fails);
    check (regs(1) = 1, "reset preserves r1 shadow", checks, fails);
    check (ram(16#100#) = 1, "reset preserves RAM shadow", checks, fails);
    wait until rising_edge(clk);
    wait until rising_edge(clk);
    wait for 1 ns;
    check (pc_out = x"0000", "reset PC stays 0", checks, fails);
    check (nuart = 1, "reset produced no UART byte", checks, fails);

    -- Release and run again: branch on r7=1 is not taken, so HALT at pc 1.
    wait until falling_edge(clk);
    rst <= '0';
    halted := false;
    cycles := 0;
    while not halted loop
      wait until falling_edge(clk);
      cycles := cycles + 1;
      if uart_tx_valid = '1' then
        nuart := nuart + 1;
        last_uart := to_integer(unsigned(uart_tx_byte));
      end if;
      if retire_valid = '1' then
        if retire_reg_write = '1' and retire_rd /= "0000" then
          regs(to_integer(unsigned(retire_rd))) := to_integer(unsigned(retire_value));
        end if;
        if retire_mem_kind = "10" then
          addr_i := to_integer(unsigned(retire_mem_addr));
          if retire_mem_size = "01" then
            if addr_i < 512 then ram(addr_i) := to_integer(unsigned(retire_mem_data(7 downto 0))); end if;
          else
            if addr_i < 512 then ram(addr_i) := to_integer(unsigned(retire_mem_data(7 downto 0))); end if;
            if addr_i + 1 < 512 then ram(addr_i + 1) := to_integer(unsigned(retire_mem_data(15 downto 8))); end if;
          end if;
        end if;
        if retire_halt = '1' then
          halted := true;
        end if;
      end if;
      if cycles > 50 then
        report "phase 2 did not halt" severity failure;
      end if;
    end loop;

    check (regs(1) = 1, "warm reset preserves r1", checks, fails);
    check (regs(7) = 1, "warm reset preserves r7", checks, fails);
    check (ram(16#100#) = 1, "warm reset preserves RAM", checks, fails);
    check (gpio_out = x"0000", "GPIO_OUT stays reset in phase2", checks, fails);
    check (nuart = 1 and last_uart = 16#34#, "no second UART byte", checks, fails);
    pc_after_halt := to_integer(unsigned(pc_out));
    wait until rising_edge(clk);
    wait until falling_edge(clk);
    check (to_integer(unsigned(pc_out)) = pc_after_halt, "phase2 PC frozen", checks, fails);

    finish_tb("reset", checks, fails);
    wait;
  end process;

end architecture sim;
