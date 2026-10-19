-- MiniCPU-16 Phase 5A CPU testbench.
--
-- Program/data hex images are loaded at elaboration by impure textio functions
-- and passed to the core as generics.  The testbench writes the exact v1
-- retirement trace (docs/TRACE_FORMAT.md), a final-state file, a coverage file
-- and enforces both a cycle budget and a wall-clock watchdog.
--
-- Cycle numbering: reset is released between edges; the first post-reset cycle
-- is cycle 1.  The testbench samples the WB retirement interface at the falling
-- edge in the middle of each cycle, so the record's retire field is the WB
-- cycle defined by docs/MICROARCH_CONTRACT.md.

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use ieee.std_logic_textio.all;
use std.textio.all;
use std.env.all;

use work.minicpu16_pkg.all;

entity tb_cpu is
  generic (
    PROGRAM_HEX          : string  := "build/prog.hex";
    DATA_HEX             : string  := "build/prog.data.hex";
    TRACE_OUT            : string  := "build/rtl.trace";
    STATE_OUT            : string  := "build/rtl.state";
    MAX_CYCLES           : integer := 200000;
    FORWARDING           : boolean := false;
    UART_CYCLES_PER_BYTE : integer := 10;
    INSTR_WORDS          : integer := 2048;
    RAM_BYTES            : integer := 4096;
    GPIO_IN_VALUE        : integer := 0;
    COUNTING             : boolean := true;
    WATCHDOG_SECONDS     : integer := 30;
    DRAIN_CYCLES         : integer := 2
  );
end entity tb_cpu;

architecture sim of tb_cpu is

  constant DIGITS : string := "0123456789ABCDEF";

  impure function load_instr (path : string) return instr_image_t is
    file f : text;
    variable status : file_open_status;
    variable ln : line;
    variable w  : std_logic_vector(31 downto 0);
    variable r  : instr_image_t(0 to INSTR_WORDS - 1) := (others => (others => '0'));
    variable i  : integer := 0;
  begin
    file_open(status, f, path, read_mode);
    if status /= open_ok then
      report "tb_cpu: cannot open program image " & path severity failure;
    end if;
    while not endfile(f) loop
      readline(f, ln);
      if ln'length > 0 then
        hread(ln, w);
        if i < INSTR_WORDS then
          r(i) := w;
        end if;
        i := i + 1;
      end if;
    end loop;
    file_close(f);
    return r;
  end function;

  impure function load_data (path : string) return byte_image_t is
    file f : text;
    variable status : file_open_status;
    variable ln : line;
    variable b  : std_logic_vector(7 downto 0);
    variable r  : byte_image_t(0 to RAM_BYTES - 1) := (others => (others => '0'));
    variable i  : integer := 0;
  begin
    file_open(status, f, path, read_mode);
    if status /= open_ok then
      report "tb_cpu: cannot open data image " & path severity failure;
    end if;
    while not endfile(f) loop
      readline(f, ln);
      if ln'length > 0 then
        hread(ln, b);
        if i < RAM_BYTES then
          r(i) := b;
        end if;
        i := i + 1;
      end if;
    end loop;
    file_close(f);
    return r;
  end function;

  constant INSTR_IMAGE : instr_image_t(0 to INSTR_WORDS - 1) := load_instr(PROGRAM_HEX);
  constant DATA_IMAGE  : byte_image_t(0 to RAM_BYTES - 1) := load_data(DATA_HEX);

  function hex4 (v : std_logic_vector(15 downto 0)) return string is
    variable s : string(1 to 4);
    variable n : integer;
  begin
    for i in 0 to 3 loop
      n := to_integer(unsigned(v((3 - i) * 4 + 3 downto (3 - i) * 4)));
      s(i + 1) := DIGITS(n + 1);
    end loop;
    return s;
  end function;

  function hex8 (v : std_logic_vector(31 downto 0)) return string is
    variable s : string(1 to 8);
    variable n : integer;
  begin
    for i in 0 to 7 loop
      n := to_integer(unsigned(v((7 - i) * 4 + 3 downto (7 - i) * 4)));
      s(i + 1) := DIGITS(n + 1);
    end loop;
    return s;
  end function;

  function hex2 (v : std_logic_vector(7 downto 0)) return string is
    variable s : string(1 to 2);
  begin
    s(1) := DIGITS(to_integer(unsigned(v(7 downto 4))) + 1);
    s(2) := DIGITS(to_integer(unsigned(v(3 downto 0))) + 1);
    return s;
  end function;

  function dec (n : integer) return string is
    variable s : string(1 to 12);
    variable t : string(1 to 12) := (others => ' ');
    variable x : integer := n;
    variable i : integer := 12;
  begin
    if x = 0 then
      return "0";
    end if;
    if x < 0 then
      return integer'image(x);      -- negative values are not used in the trace
    end if;
    while x > 0 loop
      t(i) := character'val(character'pos('0') + (x mod 10));
      x := x / 10;
      i := i - 1;
    end loop;
    return t(i + 1 to 12);
  end function;

  signal clk          : std_logic := '0';
  signal rst          : std_logic := '1';
  signal gpio_in_s    : std_logic_vector(15 downto 0) := (others => '0');
  signal uart_tx_byte : std_logic_vector(7 downto 0);
  signal uart_tx_valid: std_logic;
  signal uart_busy    : std_logic;
  signal gpio_out     : std_logic_vector(15 downto 0);
  signal pc_out       : std_logic_vector(15 downto 0);

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
  signal cov              : cov_t;

  signal done : std_logic := '0';

  procedure puts (variable l : inout line; constant s : string) is
  begin
    std.textio.write(l, s);
  end procedure;

  type reg_shadow_t is array (0 to 15) of integer;
  type uart_buf_t is array (0 to 4095) of integer;

begin

  clk <= not clk after 5 ns;
  gpio_in_s <= std_logic_vector(to_unsigned(GPIO_IN_VALUE mod 65536, 16));

  dut : entity work.minicpu16_core
    generic map (
      FORWARDING => FORWARDING,
      INSTR_WORDS => INSTR_WORDS,
      RAM_BYTES => RAM_BYTES,
      UART_CYCLES_PER_BYTE => UART_CYCLES_PER_BYTE,
      COUNTING => COUNTING,
      INSTR_INIT => INSTR_IMAGE,
      DATA_INIT => DATA_IMAGE
    )
    port map (
      clk => clk, rst => rst, gpio_in => gpio_in_s,
      uart_tx_byte => uart_tx_byte, uart_tx_valid => uart_tx_valid,
      uart_busy => uart_busy, gpio_out => gpio_out, pc_out => pc_out,
      retire_valid => retire_valid, retire_pc => retire_pc,
      retire_instr => retire_instr, retire_reg_write => retire_reg_write,
      retire_rd => retire_rd, retire_value => retire_value,
      retire_mem_kind => retire_mem_kind, retire_mem_addr => retire_mem_addr,
      retire_mem_size => retire_mem_size, retire_mem_data => retire_mem_data,
      retire_taken => retire_taken, retire_target => retire_target,
      retire_halt => retire_halt, cov => cov);

  -- Wall-clock watchdog: a hung CPU must never hang the build.
  watchdog : process
  begin
    wait for WATCHDOG_SECONDS * 1 sec;
    if done /= '1' then
      report "tb_cpu: WALL-CLOCK WATCHDOG expired" severity failure;
    end if;
    wait;
  end process;

  main : process
    file trace_f : text;
    file state_f : text;
    variable tstatus : file_open_status;
    variable sstatus : file_open_status;
    variable ln : line;
    variable cycle : integer := 0;
    variable seq   : integer := 0;
    variable total_cycles : integer := 0;
    variable final_pc : integer := 0;
    variable fetch_pc : integer := 0;
    variable halt_fetch_pc : integer := 0;
    variable halted : boolean := false;
    variable regs : reg_shadow_t := (others => 0);
    variable ram  : byte_image_t(0 to RAM_BYTES - 1) := DATA_IMAGE;
    variable uart : uart_buf_t := (others => 0);
    variable nuart : integer := 0;
    variable addr_i : integer;
    variable hx : line;
    variable size_i : integer;
    variable rd_i : integer;
  begin
    file_open(tstatus, trace_f, TRACE_OUT, write_mode);
    if tstatus /= open_ok then
      report "tb_cpu: cannot open trace file " & TRACE_OUT severity failure;
    end if;
    file_open(sstatus, state_f, STATE_OUT, write_mode);
    if sstatus /= open_ok then
      report "tb_cpu: cannot open state file " & STATE_OUT severity failure;
    end if;

    puts(ln, "# minicpu16-retirement-trace v1");
    writeline(trace_f, ln);
    puts(ln, "# seq retire pc instr rd value memop memaddr memsize memdata taken target");
    writeline(trace_f, ln);

    rst <= '1';
    wait until rising_edge(clk);
    wait until rising_edge(clk);
    wait until rising_edge(clk);
    wait until falling_edge(clk);
    rst <= '0';
    cycle := 1;
    wait until rising_edge(clk);   -- end of cycle 1
    cycle := 2;

    loop
      wait until falling_edge(clk);

      -- One-cycle UART output pulse; sample once.
      if uart_tx_valid = '1' then
        if nuart < 4096 then
          uart(nuart) := to_integer(unsigned(uart_tx_byte));
          nuart := nuart + 1;
        end if;
      end if;

      if retire_valid = '1' then
        seq := seq + 1;

        -- Retirement record.
        puts(ln, dec(seq)); puts(ln, " ");
        puts(ln, dec(cycle)); puts(ln, " ");
        puts(ln, hex4(retire_pc)); puts(ln, " ");
        puts(ln, hex8(retire_instr)); puts(ln, " ");

        rd_i := to_integer(unsigned(retire_rd));
        if retire_reg_write = '1' and rd_i /= 0 then
          puts(ln, "r" & dec(rd_i)); puts(ln, " ");
          puts(ln, hex4(retire_value)); puts(ln, " ");
          if rd_i /= 0 then
            regs(rd_i) := to_integer(unsigned(retire_value));
          end if;
        else
          puts(ln, "- "); puts(ln, "- ");
        end if;

        if retire_mem_kind = "01" then
          puts(ln, "L ");
        elsif retire_mem_kind = "10" then
          puts(ln, "S ");
        else
          puts(ln, "- ");
        end if;

        if retire_mem_kind = "01" or retire_mem_kind = "10" then
          puts(ln, hex4(retire_mem_addr)); puts(ln, " ");
        else
          puts(ln, "- ");
        end if;

        if retire_mem_size = "01" then
          puts(ln, "1 ");
        elsif retire_mem_size = "10" then
          puts(ln, "2 ");
        else
          puts(ln, "- ");
        end if;

        if retire_mem_kind = "01" or retire_mem_kind = "10" then
          puts(ln, hex4(retire_mem_data)); puts(ln, " ");
        else
          puts(ln, "- ");
        end if;

        if retire_taken = '1' then
          puts(ln, "1 ");
          puts(ln, hex4(retire_target));
        else
          puts(ln, "0 -");
        end if;
        writeline(trace_f, ln);

        -- Architectural shadow for the final state.
        if retire_mem_kind = "10" then
          addr_i := to_integer(unsigned(retire_mem_addr));
          if retire_mem_size = "01" then
            if addr_i < RAM_BYTES then
              ram(addr_i) := retire_mem_data(7 downto 0);
            end if;
          else
            if addr_i < RAM_BYTES then
              ram(addr_i) := retire_mem_data(7 downto 0);
            end if;
            if addr_i + 1 < RAM_BYTES then
              ram(addr_i + 1) := retire_mem_data(15 downto 8);
            end if;
          end if;
        end if;

        if retire_halt = '1' then
          total_cycles := cycle;
          final_pc := to_integer(unsigned(retire_pc));
          fetch_pc := to_integer(unsigned(pc_out));
          halted := true;
          exit;
        end if;
      end if;

      if cycle >= MAX_CYCLES then
        report "tb_cpu: CYCLE BUDGET exceeded (" & dec(MAX_CYCLES) & ")" severity failure;
      end if;

      wait until rising_edge(clk);
      cycle := cycle + 1;
    end loop;

    -- HALT must stop the machine.  Continue for a few cycles: no younger
    -- instruction may retire, and the frozen fetch PC must not move.  This is
    -- what catches a HALT implementation that stops fetch but lets an already
    -- fetched younger instruction drain through EX/MEM/WB.
    halt_fetch_pc := to_integer(unsigned(pc_out));
    for i in 1 to DRAIN_CYCLES loop
      wait until rising_edge(clk);
      wait until falling_edge(clk);
      if retire_valid = '1' then
        report "tb_cpu: an instruction retired after HALT" severity failure;
      end if;
      if to_integer(unsigned(pc_out)) /= halt_fetch_pc then
        report "tb_cpu: PC moved after HALT" severity failure;
      end if;
    end loop;

    -- Final state file: simple key/value lines, one item per line.
    puts(hx, "halted " & dec(1)); writeline(state_f, hx);
    puts(hx, "cycles " & dec(total_cycles)); writeline(state_f, hx);
    puts(hx, "instructions " & dec(seq)); writeline(state_f, hx);
    puts(hx, "pc " & hex4(std_logic_vector(to_unsigned(final_pc, 16)))); writeline(state_f, hx);
    puts(hx, "fetch_pc " & hex4(std_logic_vector(to_unsigned(fetch_pc, 16)))); writeline(state_f, hx);
    puts(hx, "gpio_out " & hex4(gpio_out)); writeline(state_f, hx);
    puts(hx, "stalls " & dec(to_integer(unsigned(cov(COV_STALL_TOTAL))))); writeline(state_f, hx);
    puts(hx, "flushes " & dec(to_integer(unsigned(cov(COV_FLUSH))))); writeline(state_f, hx);
    for i in 0 to 15 loop
      puts(hx, "reg " & dec(i) & " ");
      puts(hx, hex4(std_logic_vector(to_unsigned(regs(i) mod 65536, 16))));
      writeline(state_f, hx);
    end loop;
    puts(hx, "uart");
    for i in 0 to nuart - 1 loop
      puts(hx, " " & dec(uart(i)));
    end loop;
    writeline(state_f, hx);
    puts(hx, "cov");
    for i in 0 to COV_N - 1 loop
      puts(hx, " " & dec(to_integer(unsigned(cov(i)))));
    end loop;
    writeline(state_f, hx);
    puts(hx, "ram ");
    for i in 0 to RAM_BYTES - 1 loop
      puts(hx, hex2(ram(i)));
    end loop;
    writeline(state_f, hx);

    file_close(trace_f);
    file_close(state_f);
    done <= '1';
    report "tb_cpu: PASS cycles=" & dec(total_cycles) & " instructions=" & dec(seq) severity note;
    finish;
    wait;
  end process;

end architecture sim;
