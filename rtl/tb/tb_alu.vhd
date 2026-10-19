-- Self-checking testbench for rtl/alu.vhd.
-- Directed edge cross product plus 200,000 deterministic random pairs,
-- compared against independent integer arithmetic.

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use std.env.all;

use work.minicpu16_pkg.all;
use work.tb_util.all;

entity tb_alu is
end entity tb_alu;

architecture sim of tb_alu is

  type int_array is array (natural range <>) of integer;

  constant EDGES : int_array := (
    16#0000#, 16#0001#, 16#0002#, 16#7FFF#, 16#8000#, 16#8001#,
    16#FFFF#, 16#00FF#, 16#FF00#, 16#1234#, 16#1235#, 16#5555#, 16#AAAA#);

  constant ALU_OPS : int_array := (
    16#01#, 16#02#, 16#03#, 16#04#, 16#05#, 16#06#, 16#07#, 16#08#,
    16#09#, 16#0A#, 16#0B#, 16#0C#, 16#0D#, 16#0E#, 16#0F#, 16#10#,
    16#11#, 16#12#, 16#13#);

  signal op     : std_logic_vector(7 downto 0) := (others => '0');
  signal a      : std_logic_vector(15 downto 0) := (others => '0');
  signal b      : std_logic_vector(15 downto 0) := (others => '0');
  signal result : std_logic_vector(15 downto 0);

  function s16 (v : integer) return integer is
    variable x : integer;
  begin
    x := v mod 65536;
    if x < 0 then x := x + 65536; end if;
    if x >= 32768 then return x - 65536; else return x; end if;
  end function;

  function ref_alu (opc, av, bv : integer) return integer is
    variable ua, ub : integer;
    variable sh, q : integer;
  begin
    ua := av mod 65536; if ua < 0 then ua := ua + 65536; end if;
    ub := bv mod 65536; if ub < 0 then ub := ub + 65536; end if;
    case opc is
      when 16#01# | 16#0B# => return (ua + ub) mod 65536;
      when 16#02# => return (ua - ub) mod 65536;
      when 16#03# | 16#0C# => return to_integer(to_unsigned(ua, 16) and to_unsigned(ub, 16));
      when 16#04# | 16#0D# => return to_integer(to_unsigned(ua, 16) or to_unsigned(ub, 16));
      when 16#05# | 16#0E# => return to_integer(to_unsigned(ua, 16) xor to_unsigned(ub, 16));
      when 16#06# | 16#0F# => return (ua * (2 ** (ub mod 16))) mod 65536;
      when 16#07# | 16#10# => return ua / (2 ** (ub mod 16));
      when 16#08# | 16#11# =>
        -- arithmetic right shift: floor division, then 16-bit pattern
        sh := ub mod 16;
        if sh = 0 then
          q := s16(ua);
        elsif s16(ua) >= 0 then
          q := s16(ua) / (2 ** sh);
        else
          q := -(((-s16(ua)) + (2 ** sh) - 1) / (2 ** sh));
        end if;
        return q mod 65536;
      when 16#09# | 16#12# =>
        if s16(ua) < s16(ub) then return 1; else return 0; end if;
      when 16#0A# | 16#13# =>
        if ua < ub then return 1; else return 0; end if;
      when others => return 0;
    end case;
  end function;

  function img (v : integer) return string is
  begin
    return integer'image(v);
  end function;

begin

  dut : entity work.alu
    port map (op => op, a => a, b => b, result => result);

  process
    variable checks : integer := 0;
    variable fails  : integer := 0;
    variable want   : integer;
    variable seed   : unsigned(31 downto 0) := x"31415926";
    variable opc    : integer;
    variable av, bv : integer;
  begin
    -- Every opcode value, including unassigned defaults.
    for o in 0 to 255 loop
      op <= std_logic_vector(to_unsigned(o, 8));
      a  <= x"8001";
      b  <= x"7FFF";
      wait for 1 ns;
      want := ref_alu(o, 16#8001#, 16#7FFF#);
      check (result = std_logic_vector(to_unsigned(want, 16)),
             "opcode=" & img(o) & " default/result", checks, fails);
    end loop;

    -- Edge cross product over all real ALU opcodes.
    for oi in ALU_OPS'range loop
      opc := ALU_OPS(oi);
      op <= std_logic_vector(to_unsigned(opc, 8));
      for ai in EDGES'range loop
        for bi in EDGES'range loop
          a <= std_logic_vector(to_unsigned(EDGES(ai), 16));
          b <= std_logic_vector(to_unsigned(EDGES(bi), 16));
          wait for 1 ns;
          want := ref_alu(opc, EDGES(ai), EDGES(bi));
          check (result = std_logic_vector(to_unsigned(want, 16)),
                 "op=" & img(opc) & " a=" & img(EDGES(ai)) & " b=" & img(EDGES(bi)),
                 checks, fails);
        end loop;
      end loop;
    end loop;

    -- Deterministic random pairs.
    for i in 1 to 200000 loop
      seed := resize(seed * 1664525 + 1013904223, 32);
      av := to_integer(seed(15 downto 0));
      bv := to_integer(seed(31 downto 16));
      opc := ALU_OPS(to_integer(seed(7 downto 0)) mod ALU_OPS'length);
      op <= std_logic_vector(to_unsigned(opc, 8));
      a  <= std_logic_vector(to_unsigned(av, 16));
      b  <= std_logic_vector(to_unsigned(bv, 16));
      wait for 1 ns;
      want := ref_alu(opc, av, bv);
      check (result = std_logic_vector(to_unsigned(want, 16)),
             "random op=" & img(opc) & " a=" & img(av) & " b=" & img(bv),
             checks, fails);
    end loop;

    finish_tb("alu", checks, fails);
    wait;
  end process;

end architecture sim;
