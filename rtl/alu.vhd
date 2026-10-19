-- MiniCPU-16 ALU: ADD/SUB/logic/compare/shift.
-- The shifter is a separate leaf module so it can be verified exhaustively.
-- All operations are 16-bit; immediates arrive in b with use_imm already
-- resolved by the core (b = imm16 for I-type ALU instructions).

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

use work.minicpu16_pkg.all;

entity alu is
  port (
    op     : in  std_logic_vector(7 downto 0);
    a      : in  std_logic_vector(15 downto 0);
    b      : in  std_logic_vector(15 downto 0);
    result : out std_logic_vector(15 downto 0)
  );
end entity alu;

architecture rtl of alu is

  signal shift_data   : std_logic_vector(15 downto 0);
  signal shift_amount : std_logic_vector(3 downto 0);
  signal shift_mode   : std_logic_vector(1 downto 0);
  signal shift_result : std_logic_vector(15 downto 0);

  signal shift_op : std_logic;
  signal alu_r    : std_logic_vector(15 downto 0);

begin

  shift_data   <= a;
  shift_amount <= b(3 downto 0);

  with op select shift_mode <=
    SHIFT_SLL when OP_SLL | OP_SLLI,
    SHIFT_SRL when OP_SRL | OP_SRLI,
    SHIFT_SRA when others;

  with op select shift_op <=
    '1' when OP_SLL | OP_SLLI | OP_SRL | OP_SRLI | OP_SRA | OP_SRAI,
    '0' when others;

  u_shifter : entity work.shifter
    port map (
      data   => shift_data,
      amount => shift_amount,
      mode   => shift_mode,
      result => shift_result
    );

  process (all)
  begin
    alu_r <= (others => '0');
    case op is
      when OP_ADD | OP_ADDI =>
        alu_r <= std_logic_vector(unsigned(a) + unsigned(b));
      when OP_SUB =>
        alu_r <= std_logic_vector(unsigned(a) - unsigned(b));
      when OP_AND | OP_ANDI =>
        alu_r <= a and b;
      when OP_OR | OP_ORI =>
        alu_r <= a or b;
      when OP_XOR | OP_XORI =>
        alu_r <= a xor b;
      when OP_SLT | OP_SLTI =>
        if signed(a) < signed(b) then
          alu_r <= x"0001";
        else
          alu_r <= x"0000";
        end if;
      when OP_SLTU | OP_SLTIU =>
        if unsigned(a) < unsigned(b) then
          alu_r <= x"0001";
        else
          alu_r <= x"0000";
        end if;
      when OP_SLL | OP_SLLI | OP_SRL | OP_SRLI | OP_SRA | OP_SRAI =>
        alu_r <= (others => '0');       -- replaced below when shift_op = 1
      when others =>
        alu_r <= (others => '0');
    end case;
  end process;

  result <= shift_result when shift_op = '1' else alu_r;

end architecture rtl;
