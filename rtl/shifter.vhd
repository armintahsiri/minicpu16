-- MiniCPU-16 shifter: SLL, SRL, SRA with a 4-bit amount.
-- Pure combinational logic; IEEE numeric_std only.

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

use work.minicpu16_pkg.all;

entity shifter is
  port (
    data   : in  std_logic_vector(15 downto 0);
    amount : in  std_logic_vector(3 downto 0);
    mode   : in  std_logic_vector(1 downto 0);
    result : out std_logic_vector(15 downto 0)
  );
end entity shifter;

architecture rtl of shifter is
begin

  process (all)
    variable n : integer;
  begin
    n := to_integer(unsigned(amount));
    case mode is
      when SHIFT_SLL =>
        result <= std_logic_vector(shift_left(unsigned(data), n));
      when SHIFT_SRL =>
        result <= std_logic_vector(shift_right(unsigned(data), n));
      when SHIFT_SRA =>
        result <= std_logic_vector(shift_right(signed(data), n));
      when others =>
        -- Unused mode: behave as SRA; deterministic, no latch.
        result <= std_logic_vector(shift_right(signed(data), n));
    end case;
  end process;

end architecture rtl;
