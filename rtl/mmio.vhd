-- MiniCPU-16 MMIO page 0xFF00..0xFFFF.
-- UART_TX (write-only), UART_STATUS, GPIO_OUT, GPIO_IN (synchronised), TIMER,
-- and reserved addresses.  Reads are combinational and are captured by the core
-- at the end of MEM; writes commit at the end of MEM.
--
-- Byte accesses ignore address bit 0 and use the low byte of the selected
-- register.  LB/LBU sign/zero extend that low byte.  SB changes only the low
-- byte (except UART_TX, where any byte write transmits the low byte).
--
-- UART_TX accepts a byte iff the transmitter is not busy at the write cycle;
-- otherwise the byte is dropped.  The busy countdown gives observable busy on
-- the UART_CYCLES_PER_BYTE cycles after the write.

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

use work.minicpu16_pkg.all;

entity mmio is
  generic (
    UART_CYCLES_PER_BYTE : integer := 10
  );
  port (
    clk            : in  std_logic;
    rst            : in  std_logic;
    addr           : in  std_logic_vector(15 downto 0);
    load_en        : in  std_logic;
    load_type      : in  std_logic_vector(1 downto 0);
    store_en       : in  std_logic;
    store_type     : in  std_logic;
    wdata          : in  std_logic_vector(15 downto 0);
    rdata          : out std_logic_vector(15 downto 0);
    gpio_in        : in  std_logic_vector(15 downto 0);
    gpio_out       : out std_logic_vector(15 downto 0);
    timer_value    : in  std_logic_vector(15 downto 0);
    uart_tx_byte   : out std_logic_vector(7 downto 0);
    uart_tx_valid  : out std_logic;
    uart_busy      : out std_logic;
    uart_drop_pulse: out std_logic;
    read_count     : out std_logic_vector(31 downto 0);
    write_count    : out std_logic_vector(31 downto 0);
    drop_count     : out std_logic_vector(31 downto 0)
  );
end entity mmio;

architecture rtl of mmio is

  signal gpio_out_reg : std_logic_vector(15 downto 0) := (others => '0');
  signal gpio_sync1   : std_logic_vector(15 downto 0) := (others => '0');
  signal gpio_sync2   : std_logic_vector(15 downto 0) := (others => '0');

  signal uart_remaining : integer range 0 to UART_CYCLES_PER_BYTE := 0;
  signal uart_byte_r    : std_logic_vector(7 downto 0) := (others => '0');
  signal uart_valid_r   : std_logic := '0';
  signal uart_busy_s    : std_logic := '0';
  signal uart_drop_r    : std_logic := '0';

  signal reads_r  : unsigned(31 downto 0) := (others => '0');
  signal writes_r : unsigned(31 downto 0) := (others => '0');
  signal drops_r  : unsigned(31 downto 0) := (others => '0');

begin

  -- Combinational read mux.  The low-byte extension for LB/LBU is applied here
  -- so the core receives the final architectural value.
  process (all)
    variable reg : std_logic_vector(15 downto 0);
    variable w   : std_logic_vector(15 downto 0);
    variable b   : std_logic_vector(7 downto 0);
  begin
    reg := addr and x"FFFE";
    w   := (others => '0');
    case reg is
      when UART_TX_ADDR =>
        w := (others => '0');
      when UART_STATUS_ADDR =>
        w := (0 => uart_busy_s, others => '0');
      when GPIO_OUT_ADDR =>
        w := gpio_out_reg;
      when GPIO_IN_ADDR =>
        w := gpio_sync2;
      when TIMER_ADDR =>
        w := timer_value;
      when others =>
        w := (others => '0');
    end case;

    b := w(7 downto 0);
    if load_en = '1' and load_type /= LOAD_LW then
      if load_type = LOAD_LB and b(7) = '1' then
        rdata <= x"FF" & b;
      else
        rdata <= x"00" & b;
      end if;
    else
      rdata <= w;
    end if;
  end process;

  -- Busy is combinational: exactly N observable cycles c+1..c+N after an
  -- accepting write in MEM cycle c.  A registered flag would be observable for
  -- N+1 cycles and would disagree with the write-acceptance test (F25).
  uart_busy_s <= '1' when uart_remaining > 0 else '0';
  uart_busy <= uart_busy_s;
  gpio_out  <= gpio_out_reg;
  uart_tx_byte  <= uart_byte_r;
  uart_tx_valid <= uart_valid_r;
  uart_drop_pulse <= uart_drop_r;
  read_count  <= std_logic_vector(reads_r);
  write_count <= std_logic_vector(writes_r);
  drop_count  <= std_logic_vector(drops_r);

  process (clk)
    variable reg : std_logic_vector(15 downto 0);
  begin
    if rising_edge(clk) then
      if rst = '1' then
        gpio_out_reg <= (others => '0');
        gpio_sync1   <= (others => '0');
        gpio_sync2   <= (others => '0');
        uart_remaining <= 0;
        uart_byte_r    <= (others => '0');
        uart_valid_r   <= '0';
        uart_drop_r    <= '0';
        reads_r  <= (others => '0');
        writes_r <= (others => '0');
        drops_r  <= (others => '0');
      else
        -- Two-flop synchroniser for the asynchronous GPIO input.
        gpio_sync1 <= gpio_in;
        gpio_sync2 <= gpio_sync1;

        uart_valid_r <= '0';
        uart_drop_r  <= '0';
        if uart_remaining > 0 then
          uart_remaining <= uart_remaining - 1;
        end if;

        if load_en = '1' and addr(15 downto 8) = x"FF" then
          reads_r <= reads_r + 1;
        end if;

        if store_en = '1' and addr(15 downto 8) = x"FF" then
          writes_r <= writes_r + 1;
          reg := addr and x"FFFE";
          case reg is
            when UART_TX_ADDR =>
              if uart_remaining = 0 then
                uart_byte_r  <= wdata(7 downto 0);
                uart_valid_r <= '1';
                uart_remaining <= UART_CYCLES_PER_BYTE;
              else
                uart_drop_r <= '1';
                drops_r <= drops_r + 1;
              end if;
            when GPIO_OUT_ADDR =>
              if store_type = '1' then
                gpio_out_reg <= wdata;
              else
                gpio_out_reg(7 downto 0) <= wdata(7 downto 0);
              end if;
            when others =>
              null;             -- read-only or reserved: ignore the write
          end case;
        end if;
      end if;
    end if;
  end process;

end architecture rtl;
