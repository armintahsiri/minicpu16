-- MiniCPU-16 forwarding unit (structural; active only when FORWARDING=true).
-- Selects the EX operand sources.  R0 is never forwarded.  EX/MEM is younger
-- than MEM/WB and wins.  A load in EX/MEM does not forward its ALU result
-- because the load data does not exist there; the load-use hazard handles it.

library ieee;
use ieee.std_logic_1164.all;

use work.minicpu16_pkg.all;

entity forwarding_unit is
  generic (
    FORWARDING : boolean := false
  );
  port (
    id_rs1        : in  std_logic_vector(3 downto 0);
    id_rs2        : in  std_logic_vector(3 downto 0);

    ex_valid      : in  std_logic;
    ex_reg_write  : in  std_logic;
    ex_rd         : in  std_logic_vector(3 downto 0);
    ex_is_load    : in  std_logic;

    mem_valid     : in  std_logic;
    mem_reg_write : in  std_logic;
    mem_rd        : in  std_logic_vector(3 downto 0);

    sel_a         : out std_logic_vector(1 downto 0);
    sel_b         : out std_logic_vector(1 downto 0)
  );
end entity forwarding_unit;

architecture rtl of forwarding_unit is

  function select_operand (
    reg     : std_logic_vector(3 downto 0);
    exv     : std_logic;
    exrw    : std_logic;
    exrd    : std_logic_vector(3 downto 0);
    exld    : std_logic;
    memv    : std_logic;
    memrw   : std_logic;
    memrd   : std_logic_vector(3 downto 0))
    return std_logic_vector is
    variable s : std_logic_vector(1 downto 0);
  begin
    s := FWD_ID;
    if reg /= "0000" then
      if exv = '1' and exrw = '1' and exrd = reg then
        if exld = '1' then
          s := FWD_ID;                  -- never forward a load from EX/MEM
        else
          s := FWD_EXMEM;
        end if;
      elsif memv = '1' and memrw = '1' and memrd = reg then
        s := FWD_MEMWB;
      end if;
    end if;
    return s;
  end function;

begin

  process (all)
    variable a, b : std_logic_vector(1 downto 0);
  begin
    if not FORWARDING then
      sel_a <= FWD_ID;
      sel_b <= FWD_ID;
    else
      a := select_operand(id_rs1, ex_valid, ex_reg_write, ex_rd, ex_is_load,
                          mem_valid, mem_reg_write, mem_rd);
      b := select_operand(id_rs2, ex_valid, ex_reg_write, ex_rd, ex_is_load,
                          mem_valid, mem_reg_write, mem_rd);
      sel_a <= a;
      sel_b <= b;
    end if;
  end process;

end architecture rtl;
