-- MiniCPU-16 constants and types.
-- Phase 4: this is the ONLY VHDL design unit in the repository.  It contains no
-- behaviour, only the frozen ISA encoding, register aliases and memory map.
-- It must analyse cleanly with both NVC and GHDL in VHDL-2008 mode.

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

package minicpu16_pkg is

  -- ---------------------------------------------------------------------
  -- Machine size
  -- ---------------------------------------------------------------------
  constant XLEN          : integer := 16;      -- data word width
  constant INSTR_WIDTH   : integer := 32;      -- instruction width
  constant NUM_REGS      : integer := 16;
  constant REG_ADDR_BITS : integer := 4;

  -- Default sizes.  Phase 5 will make these generics of the top level; the
  -- package records the defaults and the architectural limits.
  constant INSTR_WORDS     : integer := 2048;  -- 8 KiB of instruction memory
  constant RAM_BYTES       : integer := 4096;  -- 4 KiB data RAM (default)
  constant RAM_BYTES_MAX   : integer := 16#FF00#; -- MMIO starts at 0xFF00
  constant RAM_WORDS       : integer := RAM_BYTES / 2;
  constant RAM_ADDR_BITS   : integer := 12;

  -- ---------------------------------------------------------------------
  -- Instruction field positions (bits of the 32-bit instruction word)
  -- ---------------------------------------------------------------------
  constant OPCODE_MSB : integer := 31;
  constant OPCODE_LSB : integer := 24;
  constant RD_MSB     : integer := 23;
  constant RD_LSB     : integer := 20;
  constant RS1_MSB    : integer := 19;
  constant RS1_LSB    : integer := 16;
  constant RS2_MSB    : integer := 15;
  constant RS2_LSB    : integer := 12;
  constant IMM_MSB    : integer := 15;
  constant IMM_LSB    : integer := 0;

  -- Format tags used by the assembler/checker documentation.
  constant FMT_R  : integer := 0;
  constant FMT_I  : integer := 1;
  constant FMT_S  : integer := 2;
  constant FMT_B  : integer := 3;
  constant FMT_UJ : integer := 4;
  constant FMT_HALT : integer := 5;

  -- ---------------------------------------------------------------------
  -- Opcodes (bits 31:24)
  -- 0x00 is HALT so that zero-filled instruction memory halts safely.
  -- 0x21..0x2F are reserved for a future MUL/DIV extension.
  -- All unassigned opcodes execute as NOP.
  -- ---------------------------------------------------------------------
  constant OP_HALT  : std_logic_vector(7 downto 0) := x"00";
  constant OP_ADD   : std_logic_vector(7 downto 0) := x"01";
  constant OP_SUB   : std_logic_vector(7 downto 0) := x"02";
  constant OP_AND   : std_logic_vector(7 downto 0) := x"03";
  constant OP_OR    : std_logic_vector(7 downto 0) := x"04";
  constant OP_XOR   : std_logic_vector(7 downto 0) := x"05";
  constant OP_SLL   : std_logic_vector(7 downto 0) := x"06";
  constant OP_SRL   : std_logic_vector(7 downto 0) := x"07";
  constant OP_SRA   : std_logic_vector(7 downto 0) := x"08";
  constant OP_SLT   : std_logic_vector(7 downto 0) := x"09";
  constant OP_SLTU  : std_logic_vector(7 downto 0) := x"0A";
  constant OP_ADDI  : std_logic_vector(7 downto 0) := x"0B";
  constant OP_ANDI  : std_logic_vector(7 downto 0) := x"0C";
  constant OP_ORI   : std_logic_vector(7 downto 0) := x"0D";
  constant OP_XORI  : std_logic_vector(7 downto 0) := x"0E";
  constant OP_SLLI  : std_logic_vector(7 downto 0) := x"0F";
  constant OP_SRLI  : std_logic_vector(7 downto 0) := x"10";
  constant OP_SRAI  : std_logic_vector(7 downto 0) := x"11";
  constant OP_SLTI  : std_logic_vector(7 downto 0) := x"12";
  constant OP_SLTIU : std_logic_vector(7 downto 0) := x"13";
  constant OP_LB    : std_logic_vector(7 downto 0) := x"14";
  constant OP_LBU   : std_logic_vector(7 downto 0) := x"15";
  constant OP_LW    : std_logic_vector(7 downto 0) := x"16";
  constant OP_SB    : std_logic_vector(7 downto 0) := x"17";
  constant OP_SW    : std_logic_vector(7 downto 0) := x"18";
  constant OP_BEQ   : std_logic_vector(7 downto 0) := x"19";
  constant OP_BNE   : std_logic_vector(7 downto 0) := x"1A";
  constant OP_BLT   : std_logic_vector(7 downto 0) := x"1B";
  constant OP_BGE   : std_logic_vector(7 downto 0) := x"1C";
  constant OP_BLTU  : std_logic_vector(7 downto 0) := x"1D";
  constant OP_BGEU  : std_logic_vector(7 downto 0) := x"1E";
  constant OP_JAL   : std_logic_vector(7 downto 0) := x"1F";
  constant OP_JALR  : std_logic_vector(7 downto 0) := x"20";

  constant OP_MUL_RESERVED   : std_logic_vector(7 downto 0) := x"21";
  constant OP_MULH_RESERVED  : std_logic_vector(7 downto 0) := x"22";
  constant OP_DIV_RESERVED   : std_logic_vector(7 downto 0) := x"23";
  constant OP_DIVU_RESERVED  : std_logic_vector(7 downto 0) := x"24";
  constant OP_REM_RESERVED   : std_logic_vector(7 downto 0) := x"25";
  constant OP_REMU_RESERVED  : std_logic_vector(7 downto 0) := x"26";
  constant OP_RESERVED_LAST  : std_logic_vector(7 downto 0) := x"2F";

  -- Canonical instruction words.
  constant INSTR_HALT : std_logic_vector(31 downto 0) := x"00000000";
  constant INSTR_NOP  : std_logic_vector(31 downto 0) := x"01000000"; -- ADD R0,R0,R0

  -- ---------------------------------------------------------------------
  -- Register aliases (ABI; see docs/ABI.md)
  -- ---------------------------------------------------------------------
  constant REG_ZERO : integer range 0 to 15 := 0;
  constant REG_A0   : integer range 0 to 15 := 1;
  constant REG_A1   : integer range 0 to 15 := 2;
  constant REG_A2   : integer range 0 to 15 := 3;
  constant REG_A3   : integer range 0 to 15 := 4;
  constant REG_T0   : integer range 0 to 15 := 5;
  constant REG_T1   : integer range 0 to 15 := 6;
  constant REG_T2   : integer range 0 to 15 := 7;
  constant REG_T3   : integer range 0 to 15 := 8;
  constant REG_S0   : integer range 0 to 15 := 9;
  constant REG_S1   : integer range 0 to 15 := 10;
  constant REG_S2   : integer range 0 to 15 := 11;
  constant REG_S3   : integer range 0 to 15 := 12;
  constant REG_S4   : integer range 0 to 15 := 13;
  constant REG_SP   : integer range 0 to 15 := 14;
  constant REG_LR   : integer range 0 to 15 := 15;
  constant REG_RA   : integer range 0 to 15 := 15;

  -- ---------------------------------------------------------------------
  -- Data memory map and MMIO
  -- ---------------------------------------------------------------------
  constant RAM_START       : std_logic_vector(15 downto 0) := x"0000";
  constant RAM_END_DEFAULT : std_logic_vector(15 downto 0) := x"0FFF"; -- RAM_BYTES-1
  constant UNMAPPED_START  : std_logic_vector(15 downto 0) := x"1000"; -- default
  constant UNMAPPED_END    : std_logic_vector(15 downto 0) := x"FEFF";
  constant MMIO_BASE       : std_logic_vector(15 downto 0) := x"FF00";
  constant MMIO_LAST       : std_logic_vector(15 downto 0) := x"FFFF";
  constant UART_TX_ADDR    : std_logic_vector(15 downto 0) := x"FF00";
  constant UART_STATUS_ADDR: std_logic_vector(15 downto 0) := x"FF02";
  constant GPIO_OUT_ADDR   : std_logic_vector(15 downto 0) := x"FF04";
  constant GPIO_IN_ADDR    : std_logic_vector(15 downto 0) := x"FF06";
  constant TIMER_ADDR      : std_logic_vector(15 downto 0) := x"FF08";
  constant MMIO_RSVD_START : std_logic_vector(15 downto 0) := x"FF0A";

  constant UART_STATUS_TX_BUSY_BIT : integer := 0;
  constant UART_CYCLES_PER_BYTE_DEFAULT : integer := 10;
  constant MMIO_WORD_LSB : integer := 1; -- bit 0 of an MMIO address is ignored

  -- Selector encodings used by the leaf modules.
  constant SHIFT_SLL : std_logic_vector(1 downto 0) := "00";
  constant SHIFT_SRL : std_logic_vector(1 downto 0) := "01";
  constant SHIFT_SRA : std_logic_vector(1 downto 0) := "10";
  constant LOAD_LB   : std_logic_vector(1 downto 0) := "00";
  constant LOAD_LBU  : std_logic_vector(1 downto 0) := "01";
  constant LOAD_LW   : std_logic_vector(1 downto 0) := "10";
  constant FWD_ID    : std_logic_vector(1 downto 0) := "00";
  constant FWD_EXMEM : std_logic_vector(1 downto 0) := "01";
  constant FWD_MEMWB : std_logic_vector(1 downto 0) := "10";

  -- ---------------------------------------------------------------------
  -- Memory array types for generated images (BRAM-friendly)
  -- ---------------------------------------------------------------------
  type instr_rom_t is array (0 to INSTR_WORDS - 1) of std_logic_vector(31 downto 0);
  type byte_bank_t is array (0 to RAM_WORDS - 1) of std_logic_vector(7 downto 0);

  -- Unconstrained image types used by the Phase 5 core and testbench.  The
  -- testbench loads a hex file at elaboration and passes the result as a
  -- generic; the core pads/constrains it to its own INSTR_WORDS/RAM_BYTES.
  type instr_image_t is array (natural range <>) of std_logic_vector(31 downto 0);
  type byte_image_t  is array (natural range <>) of std_logic_vector(7 downto 0);

  -- ---------------------------------------------------------------------
  -- Decoded control bundle carried in the ID/EX, EX/MEM and MEM/WB
  -- registers.  Every field is assigned by rtl/decoder.vhd.
  --
  --   mem_size = '1' -> word (LW/SW), '0' -> byte (LB/LBU/SB)
  --   use_imm  = '1' -> the ALU b operand is imm16, not rs2
  --   rs1_en/rs2_en mark actual architectural sources (hazard/forwarding)
  -- ---------------------------------------------------------------------
  type ctrl_t is record
    is_halt    : std_logic;
    is_nop     : std_logic;
    is_alu     : std_logic;
    is_load    : std_logic;
    is_store   : std_logic;
    is_branch  : std_logic;
    is_jal     : std_logic;
    is_jalr    : std_logic;
    use_imm    : std_logic;
    reg_write  : std_logic;
    mem_read   : std_logic;
    mem_write  : std_logic;
    mem_size   : std_logic;
    rs1_en     : std_logic;
    rs2_en     : std_logic;
    rd         : std_logic_vector(3 downto 0);
    rs1        : std_logic_vector(3 downto 0);
    rs2        : std_logic_vector(3 downto 0);
    op         : std_logic_vector(7 downto 0);
    imm        : std_logic_vector(15 downto 0);
  end record;

  constant CTRL_ZERO : ctrl_t := (
    is_halt => '0', is_nop => '0', is_alu => '0', is_load => '0',
    is_store => '0', is_branch => '0', is_jal => '0', is_jalr => '0',
    use_imm => '0', reg_write => '0', mem_read => '0', mem_write => '0',
    mem_size => '0', rs1_en => '0', rs2_en => '0',
    rd => (others => '0'), rs1 => (others => '0'), rs2 => (others => '0'),
    op => (others => '0'), imm => (others => '0'));

  -- ---------------------------------------------------------------------
  -- Optional verification counters (COUNTING generic in the core).  They
  -- never drive architectural state and are tied to zero when COUNTING is
  -- false.  The CPU testbench writes them to its state file for the fuzz
  -- coverage table.
  -- ---------------------------------------------------------------------
  constant COV_N : integer := 16;
  type cov_counters_t is array (0 to COV_N - 1) of std_logic_vector(31 downto 0);
  subtype cov_t is cov_counters_t;

  constant COV_STALL_TOTAL      : integer := 0;
  constant COV_STALL_EX         : integer := 1;
  constant COV_STALL_MEM        : integer := 2;
  constant COV_STALL_RS1        : integer := 3;
  constant COV_STALL_RS2        : integer := 4;
  constant COV_STALL_STORE_DATA : integer := 5;
  constant COV_FLUSH            : integer := 6;
  constant COV_HALT_SHADOW      : integer := 7;
  constant COV_LOAD_R0          : integer := 8;
  constant COV_UART_DROP        : integer := 9;
  constant COV_MISALIGNED       : integer := 10;
  constant COV_MMIO_READ        : integer := 11;
  constant COV_MMIO_WRITE       : integer := 12;
  constant COV_STALL_LOAD_USE   : integer := 13;
  constant COV_FLUSH_BRANCH     : integer := 14;
  constant COV_FLUSH_JUMP       : integer := 15;

  -- Sign-extend a 16-bit pattern to a 16-bit signed pattern.  This is the
  -- identity as a bit pattern, but it documents the branch/address/JALR rule
  -- and avoids accidental unsigned arithmetic in the RTL.
  function sext16(v : std_logic_vector(15 downto 0)) return std_logic_vector;

end package minicpu16_pkg;

package body minicpu16_pkg is

  function sext16(v : std_logic_vector(15 downto 0)) return std_logic_vector is
  begin
    return std_logic_vector(resize(signed(v), 16));
  end function;

end package body minicpu16_pkg;
