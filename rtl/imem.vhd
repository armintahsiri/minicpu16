-- MiniCPU-16 instruction memory: synchronous read, INSTR_WORDS 32-bit words.
-- The output register is the IF/ID instruction register (see docs/RTL_ARCH.md).
-- Fetching an address >= INSTR_WORDS returns HALT (0x00000000).  The check is on
-- the registered address and selects the output-register data; it is not in
-- series with the BRAM address.

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

use work.minicpu16_pkg.all;

entity imem is
  generic (
    INSTR_WORDS : integer := 2048;
    INIT        : instr_image_t := (0 to 0 => x"00000000")
  );
  port (
    clk   : in  std_logic;
    rst   : in  std_logic;
    addr  : in  std_logic_vector(15 downto 0);
    instr : out std_logic_vector(31 downto 0)
  );
end entity imem;

architecture rtl of imem is

  type rom_t is array (0 to INSTR_WORDS - 1) of std_logic_vector(31 downto 0);

  function pad_image (src : instr_image_t; n : integer) return rom_t is
    variable r : rom_t := (others => (others => '0'));
  begin
    for i in 0 to n - 1 loop
      if i < src'length then
        r(i) := src(src'low + i);
      end if;
    end loop;
    return r;
  end function;

  signal rom : rom_t := pad_image(INIT, INSTR_WORDS);

begin

  process (clk)
    variable a : integer;
  begin
    if rising_edge(clk) then
      if rst = '1' then
        instr <= INSTR_HALT;
      else
        a := to_integer(unsigned(addr));
        if a < INSTR_WORDS then
          instr <= rom(a);
        else
          instr <= INSTR_HALT;
        end if;
      end if;
    end if;
  end process;

end architecture rtl;
