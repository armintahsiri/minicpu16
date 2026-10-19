-- MiniCPU-16 decoder: one 32-bit instruction to the control bundle.
-- Unassigned opcodes (including the reserved MUL/DIV range) are NOP.
-- Opcode 0 is HALT regardless of the low bits (ISA section 6).
-- Source-register enables describe the ACTUAL sources used by the instruction:
--   R-type: rs1, rs2
--   I-type ALU / load / JALR: rs1
--   store / branch: rs1 and the [23:20] register
--   JAL: none
--   HALT / NOP: none

library ieee;
use ieee.std_logic_1164.all;

use work.minicpu16_pkg.all;

entity decoder is
  port (
    instr : in  std_logic_vector(31 downto 0);
    ctrl  : out ctrl_t
  );
end entity decoder;

architecture rtl of decoder is
begin

  process (all)
    variable c  : ctrl_t;
    variable op : std_logic_vector(7 downto 0);
  begin
    c  := CTRL_ZERO;
    op := instr(31 downto 24);

    c.op  := op;
    c.rd  := instr(23 downto 20);
    c.rs1 := instr(19 downto 16);
    c.imm := instr(15 downto 0);

    case op is
      when OP_HALT =>
        c.is_halt := '1';

      when OP_ADD | OP_SUB | OP_AND | OP_OR | OP_XOR |
           OP_SLL | OP_SRL | OP_SRA | OP_SLT | OP_SLTU =>
        c.is_alu    := '1';
        c.reg_write := '1';
        c.rs1_en    := '1';
        c.rs2_en    := '1';
        c.rs2       := instr(15 downto 12);
        c.use_imm   := '0';

      when OP_ADDI | OP_ANDI | OP_ORI | OP_XORI |
           OP_SLLI | OP_SRLI | OP_SRAI | OP_SLTI | OP_SLTIU =>
        c.is_alu    := '1';
        c.reg_write := '1';
        c.rs1_en    := '1';
        c.use_imm   := '1';

      when OP_LB | OP_LBU | OP_LW =>
        c.is_load   := '1';
        c.mem_read  := '1';
        c.reg_write := '1';
        c.rs1_en    := '1';
        if op = OP_LW then
          c.mem_size := '1';
        else
          c.mem_size := '0';
        end if;

      when OP_SB | OP_SW =>
        c.is_store  := '1';
        c.mem_write := '1';
        c.rs1_en    := '1';
        c.rs2_en    := '1';
        c.rs2       := instr(23 downto 20);
        if op = OP_SW then
          c.mem_size := '1';
        else
          c.mem_size := '0';
        end if;

      when OP_BEQ | OP_BNE | OP_BLT | OP_BGE | OP_BLTU | OP_BGEU =>
        c.is_branch := '1';
        c.rs1_en    := '1';
        c.rs2_en    := '1';
        c.rs2       := instr(23 downto 20);

      when OP_JAL =>
        c.is_jal    := '1';
        c.reg_write := '1';

      when OP_JALR =>
        c.is_jalr   := '1';
        c.reg_write := '1';
        c.rs1_en    := '1';

      when others =>
        -- Reserved (0x21..0x2F) and every unassigned opcode: NOP.
        c.is_nop := '1';
    end case;

    ctrl <= c;
  end process;

end architecture rtl;
