-- MiniCPU-16 branch comparator: BEQ/BNE/BLT/BGE/BLTU/BGEU.
-- The target adder lives in the core; this unit only decides taken.

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

use work.minicpu16_pkg.all;

entity branch_unit is
  port (
    op    : in  std_logic_vector(7 downto 0);
    a     : in  std_logic_vector(15 downto 0);
    b     : in  std_logic_vector(15 downto 0);
    taken : out std_logic
  );
end entity branch_unit;

architecture rtl of branch_unit is
begin

  process (all)
  begin
    taken <= '0';
    case op is
      when OP_BEQ =>
        if a = b then taken <= '1'; end if;
      when OP_BNE =>
        if a /= b then taken <= '1'; end if;
      when OP_BLT =>
        if signed(a) < signed(b) then taken <= '1'; end if;
      when OP_BGE =>
        if signed(a) >= signed(b) then taken <= '1'; end if;
      when OP_BLTU =>
        if unsigned(a) < unsigned(b) then taken <= '1'; end if;
      when OP_BGEU =>
        if unsigned(a) >= unsigned(b) then taken <= '1'; end if;
      when others =>
        taken <= '0';
    end case;
  end process;

end architecture rtl;
