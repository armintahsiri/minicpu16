-- Self-checking testbench for rtl/branch_unit.vhd.
-- Edge-value cross product (600 checks) plus 100,000 deterministic random
-- pairs, compared against independent signed/unsigned integer comparisons.

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use std.env.all;

use work.minicpu16_pkg.all;
use work.tb_util.all;

entity tb_branch_unit is
end entity tb_branch_unit;

architecture sim of tb_branch_unit is

  type int_array is array (natural range <>) of integer;
  constant EDGES : int_array := (
    16#0000#, 16#0001#, 16#7FFF#, 16#8000#, 16#8001#,
    16#FFFF#, 16#00FF#, 16#FF00#, 16#1234#, 16#1235#);

  constant OPS : int_array := (16#19#, 16#1A#, 16#1B#, 16#1C#, 16#1D#, 16#1E#);

  signal op    : std_logic_vector(7 downto 0) := (others => '0');
  signal a     : std_logic_vector(15 downto 0) := (others => '0');
  signal b     : std_logic_vector(15 downto 0) := (others => '0');
  signal taken : std_logic;

  function s16 (v : integer) return integer is
    variable x : integer;
  begin
    x := v mod 65536;
    if x < 0 then x := x + 65536; end if;
    if x >= 32768 then return x - 65536; else return x; end if;
  end function;

  function ref_taken (opc, av, bv : integer) return boolean is
    variable ua, ub : integer;
  begin
    ua := av mod 65536; if ua < 0 then ua := ua + 65536; end if;
    ub := bv mod 65536; if ub < 0 then ub := ub + 65536; end if;
    case opc is
      when 16#19# => return ua = ub;
      when 16#1A# => return ua /= ub;
      when 16#1B# => return s16(ua) < s16(ub);
      when 16#1C# => return s16(ua) >= s16(ub);
      when 16#1D# => return ua < ub;
      when 16#1E# => return ua >= ub;
      when others => return false;
    end case;
  end function;

  function img (v : integer) return string is
  begin
    return integer'image(v);
  end function;

begin

  dut : entity work.branch_unit
    port map (op => op, a => a, b => b, taken => taken);

  process
    variable checks : integer := 0;
    variable fails  : integer := 0;
    variable seed   : unsigned(31 downto 0) := x"0BADF00D";
    variable opc    : integer;
    variable av, bv : integer;
    variable ok     : boolean;
  begin
    for oi in OPS'range loop
      opc := OPS(oi);
      op <= std_logic_vector(to_unsigned(opc, 8));
      for ai in EDGES'range loop
        for bi in EDGES'range loop
          a <= std_logic_vector(to_unsigned(EDGES(ai), 16));
          b <= std_logic_vector(to_unsigned(EDGES(bi), 16));
          wait for 1 ns;
          if ref_taken(opc, EDGES(ai), EDGES(bi)) then ok := taken = '1'; else ok := taken = '0'; end if;
          check (ok,
                 "op=" & img(opc) & " a=" & img(EDGES(ai)) & " b=" & img(EDGES(bi)),
                 checks, fails);
        end loop;
      end loop;
    end loop;

    for i in 1 to 100000 loop
      seed := resize(seed * 1103515245 + 12345, 32);
      av := to_integer(seed(15 downto 0));
      bv := to_integer(seed(31 downto 16));
      opc := OPS(to_integer(seed(7 downto 0)) mod OPS'length);
      op <= std_logic_vector(to_unsigned(opc, 8));
      a  <= std_logic_vector(to_unsigned(av, 16));
      b  <= std_logic_vector(to_unsigned(bv, 16));
      wait for 1 ns;
      if ref_taken(opc, av, bv) then ok := taken = '1'; else ok := taken = '0'; end if;
      check (ok,
             "random op=" & img(opc) & " a=" & img(av) & " b=" & img(bv),
             checks, fails);
    end loop;

    finish_tb("branch_unit", checks, fails);
    wait;
  end process;

end architecture sim;
