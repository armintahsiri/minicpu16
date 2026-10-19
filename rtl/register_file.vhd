-- MiniCPU-16 register file: 16 x 16, R0 hardwired to zero.
-- Two asynchronous read ports, one write port, and a WB -> ID write-through
-- bypass on both read ports.  Reset suppresses writes but does NOT clear the
-- registers: their power-on value comes from FPGA initialisation (zero here for
-- deterministic simulation).  A warm reset preserves every register.

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

use work.minicpu16_pkg.all;

entity register_file is
  port (
    clk    : in  std_logic;
    rst    : in  std_logic;
    raddr1 : in  std_logic_vector(3 downto 0);
    raddr2 : in  std_logic_vector(3 downto 0);
    rdata1 : out std_logic_vector(15 downto 0);
    rdata2 : out std_logic_vector(15 downto 0);
    we     : in  std_logic;
    waddr  : in  std_logic_vector(3 downto 0);
    wdata  : in  std_logic_vector(15 downto 0)
  );
end entity register_file;

architecture rtl of register_file is

  type reg_array_t is array (0 to 15) of std_logic_vector(15 downto 0);
  signal regs : reg_array_t := (others => (others => '0'));

begin

  -- Read ports with same-cycle WB write-through.  R0 always reads zero and
  -- never observes a write.  The write-through is gated by rst so a stale
  -- write-enable during reset cannot change an ID operand.
  process (all)
  begin
    if raddr1 = "0000" then
      rdata1 <= (others => '0');
    elsif rst = '0' and we = '1' and waddr = raddr1 and waddr /= "0000" then
      rdata1 <= wdata;
    else
      rdata1 <= regs(to_integer(unsigned(raddr1)));
    end if;

    if raddr2 = "0000" then
      rdata2 <= (others => '0');
    elsif rst = '0' and we = '1' and waddr = raddr2 and waddr /= "0000" then
      rdata2 <= wdata;
    else
      rdata2 <= regs(to_integer(unsigned(raddr2)));
    end if;
  end process;

  process (clk)
  begin
    if rising_edge(clk) then
      if rst = '0' and we = '1' and waddr /= "0000" then
        regs(to_integer(unsigned(waddr))) <= wdata;
      end if;
    end if;
  end process;

end architecture rtl;
