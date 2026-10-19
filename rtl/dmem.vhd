-- MiniCPU-16 data memory: RAM_BYTES bytes in two byte banks (even/odd).
-- Each bank is its own clocked process with one array, which is what GHDL
-- infers as a block RAM; putting both banks in one process made GHDL expand the
-- arrays to 4096 flip-flops.  Reads use the MEM-cycle address and register the
-- selected bytes; stores commit at the end of MEM.  There is deliberately no
-- reset branch in the bank processes (BRAM has no reset): the core gates
-- load/store enables with rst, and the output registers are initialised to 0.
--
-- LW/SW ignore address bit 0.  LB/LBU sign/zero extend the selected byte.
-- Addresses >= RAM_BYTES read zero and ignore writes (MMIO is separate).

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

use work.minicpu16_pkg.all;

entity dmem is
  generic (
    RAM_BYTES : integer := 4096;
    INIT      : byte_image_t := (0 to 0 => x"00")
  );
  port (
    clk        : in  std_logic;
    rst        : in  std_logic;
    addr       : in  std_logic_vector(15 downto 0);
    load_en    : in  std_logic;
    load_type  : in  std_logic_vector(1 downto 0);
    store_en   : in  std_logic;
    store_type : in  std_logic;
    wdata      : in  std_logic_vector(15 downto 0);
    rdata      : out std_logic_vector(15 downto 0)
  );
end entity dmem;

architecture rtl of dmem is

  constant WORDS : integer := RAM_BYTES / 2;
  type bank_t is array (0 to WORDS - 1) of std_logic_vector(7 downto 0);

  function pad_bank (src : byte_image_t; n : integer; odd : integer) return bank_t is
    variable r : bank_t := (others => (others => '0'));
    variable k : integer;
  begin
    for i in 0 to n - 1 loop
      k := 2 * i + odd;
      if k < src'length then
        r(i) := src(src'low + k);
      end if;
    end loop;
    return r;
  end function;

  signal even_bank : bank_t := pad_bank(INIT, WORDS, 0);
  signal odd_bank  : bank_t := pad_bank(INIT, WORDS, 1);

  signal even_q      : std_logic_vector(7 downto 0) := (others => '0');
  signal odd_q       : std_logic_vector(7 downto 0) := (others => '0');
  signal q_load_type : std_logic_vector(1 downto 0) := (others => '0');
  signal q_addr0     : std_logic := '0';
  signal odd_wdata   : std_logic_vector(7 downto 0) := (others => '0');

begin

  -- Select the byte that the odd bank writes: the high byte of a word store, or
  -- the low byte of a byte store at an odd address.  Keeping this as one signal
  -- (rather than a mux inside the clocked process) lets GHDL infer the bank as a
  -- memory, like the even bank.
  odd_wdata <= wdata(15 downto 8) when store_type = '1' else wdata(7 downto 0);

  -- Even byte bank: writes the low byte of a word, or a byte at an even address.
  process (clk)
    variable a, idx : integer;
  begin
    if rising_edge(clk) then
      a := to_integer(unsigned(addr));
      idx := a / 2;
      if store_en = '1' and a < RAM_BYTES then
        if store_type = '1' or addr(0) = '0' then
          even_bank(idx) <= wdata(7 downto 0);
        end if;
      end if;
      -- Unconditional registered read: the output register is the MEM/WB load
      -- data register.  The pipeline valid bits decide whether the value is
      -- used, and an unconditional read lets the synthesis tool merge this
      -- register into the block RAM's output register.
      if a < RAM_BYTES then
        even_q <= even_bank(idx);
      else
        even_q <= (others => '0');
      end if;
    end if;
  end process;

  -- Odd byte bank: writes the high byte of a word, or a byte at an odd address.
  process (clk)
    variable a, idx : integer;
  begin
    if rising_edge(clk) then
      a := to_integer(unsigned(addr));
      idx := a / 2;
      if store_en = '1' and a < RAM_BYTES then
        if store_type = '1' or addr(0) = '1' then
          odd_bank(idx) <= odd_wdata;
        end if;
      end if;
      if a < RAM_BYTES then
        odd_q <= odd_bank(idx);
      else
        odd_q <= (others => '0');
      end if;
    end if;
  end process;

  -- Capture the read type/address for the output mux.  This register is updated
  -- at the same edge as the bank read registers, so the WB-stage value is
  -- correct even if the next MEM instruction has a different address.
  process (clk)
  begin
    if rising_edge(clk) then
      q_load_type <= load_type;
      q_addr0     <= addr(0);
    end if;
  end process;

  process (all)
    variable b : std_logic_vector(7 downto 0);
  begin
    b := (others => '0');
    if q_addr0 = '0' then
      b := even_q;
    else
      b := odd_q;
    end if;
    if rst = '1' then
      rdata <= (others => '0');
    elsif q_load_type = LOAD_LW then
      rdata <= odd_q & even_q;
    elsif q_load_type = LOAD_LB and b(7) = '1' then
      rdata <= x"FF" & b;
    else
      rdata <= x"00" & b;
    end if;
  end process;

end architecture rtl;
