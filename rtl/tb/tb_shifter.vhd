-- Exhaustive self-checking testbench for rtl/shifter.vhd.
-- For every 16-bit data value and every 4-bit amount, in all three modes,
-- compare against an independent integer reference (not a VHDL shift operator).

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use std.env.all;

use work.minicpu16_pkg.all;
use work.tb_util.all;

entity tb_shifter is
end entity tb_shifter;

architecture sim of tb_shifter is
  signal data   : std_logic_vector(15 downto 0) := (others => '0');
  signal amount : std_logic_vector(3 downto 0) := (others => '0');
  signal mode   : std_logic_vector(1 downto 0) := SHIFT_SLL;
  signal result : std_logic_vector(15 downto 0);

  function s16 (v : integer) return integer is
    variable x : integer;
  begin
    x := v mod 65536;
    if x < 0 then x := x + 65536; end if;
    if x >= 32768 then return x - 65536; else return x; end if;
  end function;

  function floor_div (a, d : integer) return integer is
  begin
    if a >= 0 then
      return a / d;
    else
      return -(((-a) + d - 1) / d);
    end if;
  end function;

  function ref_shift (v : integer; amt : integer; m : std_logic_vector(1 downto 0)) return integer is
    variable u : integer;
    variable s : integer;
  begin
    u := v mod 65536;
    if u < 0 then u := u + 65536; end if;
    s := s16(u);
    if m = SHIFT_SLL then
      return (u * (2 ** amt)) mod 65536;
    elsif m = SHIFT_SRL then
      return u / (2 ** amt);
    else
      return floor_div(s, 2 ** amt) mod 65536;
    end if;
  end function;

  function img (v : integer) return string is
  begin
    return integer'image(v);
  end function;

begin

  dut : entity work.shifter
    port map (data => data, amount => amount, mode => mode, result => result);

  process
    variable checks : integer := 0;
    variable fails  : integer := 0;
    variable want   : integer;
    variable m      : std_logic_vector(1 downto 0);
  begin
    for v in 0 to 65535 loop
      data <= std_logic_vector(to_unsigned(v, 16));
      for a in 0 to 15 loop
        amount <= std_logic_vector(to_unsigned(a, 4));
        for mi in 0 to 2 loop
          case mi is
            when 0 => m := SHIFT_SLL;
            when 1 => m := SHIFT_SRL;
            when others => m := SHIFT_SRA;
          end case;
          mode <= m;
          wait for 1 ns;
          want := ref_shift(v, a, m);
          check (result = std_logic_vector(to_unsigned(want, 16)),
                 "data=" & img(v) & " amt=" & img(a) & " mode=" & img(mi) &
                 " got=" & img(to_integer(unsigned(result))) & " want=" & img(want),
                 checks, fails);
        end loop;
      end loop;
    end loop;

    finish_tb("shifter", checks, fails);
    wait;
  end process;

end architecture sim;
