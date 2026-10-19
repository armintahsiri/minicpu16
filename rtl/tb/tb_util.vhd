-- Shared self-checking testbench utilities.
library ieee;
use ieee.std_logic_1164.all;
use std.env.all;

package tb_util is

  procedure check (
    cond    : boolean;
    msg     : string;
    variable checks : inout integer;
    variable fails  : inout integer);

  procedure finish_tb (
    name    : string;
    checks  : integer;
    fails   : integer);

end package tb_util;

package body tb_util is

  procedure check (
    cond    : boolean;
    msg     : string;
    variable checks : inout integer;
    variable fails  : inout integer) is
  begin
    checks := checks + 1;
    if not cond then
      fails := fails + 1;
      report "FAIL: " & msg severity error;
    end if;
  end procedure;

  procedure finish_tb (
    name    : string;
    checks  : integer;
    fails   : integer) is
  begin
    if fails /= 0 then
      report name & ": " & integer'image(fails) & " failure(s) in " &
             integer'image(checks) & " checks" severity failure;
    else
      report "PASS " & name & " checks=" & integer'image(checks) severity note;
      finish;
    end if;
  end procedure;

end package body tb_util;
