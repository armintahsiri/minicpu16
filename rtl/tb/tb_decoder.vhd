-- Self-checking testbench for rtl/decoder.vhd.
-- All 256 opcodes with fixed fields, special HALT/NOP cases, and 50,000
-- deterministic random instruction words compared with an independent decoder.

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use std.env.all;

use work.minicpu16_pkg.all;
use work.tb_util.all;

entity tb_decoder is
end entity tb_decoder;

architecture sim of tb_decoder is

  signal instr : std_logic_vector(31 downto 0) := (others => '0');
  signal ctrl  : ctrl_t;

  function ref_decode (w : std_logic_vector(31 downto 0)) return ctrl_t is
    variable c  : ctrl_t;
    variable op : std_logic_vector(7 downto 0);
  begin
    c  := CTRL_ZERO;
    op := w(31 downto 24);
    c.op  := op;
    c.rd  := w(23 downto 20);
    c.rs1 := w(19 downto 16);
    c.imm := w(15 downto 0);
    case op is
      when OP_HALT => c.is_halt := '1';
      when OP_ADD | OP_SUB | OP_AND | OP_OR | OP_XOR |
           OP_SLL | OP_SRL | OP_SRA | OP_SLT | OP_SLTU =>
        c.is_alu := '1'; c.reg_write := '1'; c.rs1_en := '1'; c.rs2_en := '1';
        c.rs2 := w(15 downto 12); c.use_imm := '0';
      when OP_ADDI | OP_ANDI | OP_ORI | OP_XORI |
           OP_SLLI | OP_SRLI | OP_SRAI | OP_SLTI | OP_SLTIU =>
        c.is_alu := '1'; c.reg_write := '1'; c.rs1_en := '1'; c.use_imm := '1';
      when OP_LB | OP_LBU | OP_LW =>
        c.is_load := '1'; c.mem_read := '1'; c.reg_write := '1'; c.rs1_en := '1';
        if op = OP_LW then c.mem_size := '1'; else c.mem_size := '0'; end if;
      when OP_SB | OP_SW =>
        c.is_store := '1'; c.mem_write := '1'; c.rs1_en := '1'; c.rs2_en := '1';
        c.rs2 := w(23 downto 20);
        if op = OP_SW then c.mem_size := '1'; else c.mem_size := '0'; end if;
      when OP_BEQ | OP_BNE | OP_BLT | OP_BGE | OP_BLTU | OP_BGEU =>
        c.is_branch := '1'; c.rs1_en := '1'; c.rs2_en := '1'; c.rs2 := w(23 downto 20);
      when OP_JAL => c.is_jal := '1'; c.reg_write := '1';
      when OP_JALR => c.is_jalr := '1'; c.reg_write := '1'; c.rs1_en := '1';
      when others => c.is_nop := '1';
    end case;
    return c;
  end function;

  function make_word (op, rd, rs1, rs2, imm : integer) return std_logic_vector is
  begin
    return std_logic_vector(to_unsigned(op, 8)) &
           std_logic_vector(to_unsigned(rd, 4)) &
           std_logic_vector(to_unsigned(rs1, 4)) &
           std_logic_vector(to_unsigned((rs2 mod 16) * 4096 + (imm mod 4096), 16));
  end function;

  function img (v : integer) return string is
  begin
    return integer'image(v);
  end function;

begin

  dut : entity work.decoder
    port map (instr => instr, ctrl => ctrl);

  process
    variable checks : integer := 0;
    variable fails  : integer := 0;
    variable seed   : unsigned(31 downto 0) := x"DEC0DE01";
    variable w      : std_logic_vector(31 downto 0);
  begin
    for o in 0 to 255 loop
      w := make_word(o, 5, 6, 7, 16#1234#);
      instr <= w;
      wait for 1 ns;
      check (ctrl = ref_decode(w), "opcode " & img(o), checks, fails);
    end loop;

    -- Opcode 0 with nonzero low bits is still HALT.
    instr <= x"00FEDCBA";
    wait for 1 ns;
    check (ctrl = ref_decode(x"00FEDCBA"), "HALT ignores low bits", checks, fails);

    -- JAL ignores its rs1 field.
    instr <= make_word(16#1F#, 15, 9, 3, 16#0100#);
    wait for 1 ns;
    check (ctrl = ref_decode(instr), "JAL ignores rs1", checks, fails);
    check (ctrl.rs1_en = '0', "JAL rs1_en", checks, fails);
    check (ctrl.rs2_en = '0', "JAL rs2_en", checks, fails);

    -- Random instruction words.
    for i in 1 to 50000 loop
      seed := resize(seed * 1664525 + 1013904223, 32);
      w := std_logic_vector(seed);
      instr <= w;
      wait for 1 ns;
      check (ctrl = ref_decode(w), "random word " & img(i), checks, fails);
    end loop;

    finish_tb("decoder", checks, fails);
    wait;
  end process;

end architecture sim;
