-- MiniCPU-16 Phase 5A five-stage core.
--
--   IF  : PC -> synchronous instruction memory (output register is IF/ID instr)
--   ID  : decode, register read with WB->ID write-through, hazard detection
--   EX  : structural forwarding muxes, ALU, branch/JAL/JALR, link
--   MEM : dmem + MMIO, store commit, load capture
--   WB  : register write, retirement record
--
-- FORWARDING=false is Phase 5A: the forwarding unit selects ID/EX values and
-- the hazard unit stalls for every EX/MEM producer.  FORWARDING=true is
-- structurally present but unverified in Phase 5A.
--
-- Reset is synchronous and suppresses all architectural writes.  It does not
-- clear the register file or the data RAM.  GPIO_OUT/TIMER/UART are reset as
-- specified by docs/ISA.md section 8.
--
-- The RETIRE and cov ports are verification interfaces.  They never drive
-- architectural state; synthesis removes them when unconnected, and COUNTING
-- can be false to tie the counters to zero.

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

use work.minicpu16_pkg.all;

entity minicpu16_core is
  generic (
    FORWARDING           : boolean := false;
    INSTR_WORDS          : integer := 2048;
    RAM_BYTES            : integer := 4096;
    UART_CYCLES_PER_BYTE : integer := 10;
    COUNTING             : boolean := false;
    INSTR_INIT           : instr_image_t := (0 to 0 => x"00000000");
    DATA_INIT            : byte_image_t  := (0 to 0 => x"00")
  );
  port (
    clk            : in  std_logic;
    rst            : in  std_logic;
    gpio_in        : in  std_logic_vector(15 downto 0);
    uart_tx_byte   : out std_logic_vector(7 downto 0);
    uart_tx_valid  : out std_logic;
    uart_busy      : out std_logic;
    gpio_out       : out std_logic_vector(15 downto 0);
    pc_out         : out std_logic_vector(15 downto 0);

    retire_valid      : out std_logic;
    retire_pc         : out std_logic_vector(15 downto 0);
    retire_instr      : out std_logic_vector(31 downto 0);
    retire_reg_write  : out std_logic;
    retire_rd         : out std_logic_vector(3 downto 0);
    retire_value      : out std_logic_vector(15 downto 0);
    retire_mem_kind   : out std_logic_vector(1 downto 0);
    retire_mem_addr   : out std_logic_vector(15 downto 0);
    retire_mem_size   : out std_logic_vector(1 downto 0);
    retire_mem_data   : out std_logic_vector(15 downto 0);
    retire_taken      : out std_logic;
    retire_target     : out std_logic_vector(15 downto 0);
    retire_halt       : out std_logic;

    cov               : out cov_t
  );
end entity minicpu16_core;

architecture rtl of minicpu16_core is

  function load_type_of (c : ctrl_t) return std_logic_vector is
  begin
    if c.mem_size = '1' then
      return LOAD_LW;
    elsif c.op = OP_LB then
      return LOAD_LB;
    else
      return LOAD_LBU;
    end if;
  end function;

  -- PC and fetch
  signal pc         : unsigned(15 downto 0) := (others => '0');
  signal stop_fetch : std_logic := '0';
  signal imem_addr  : std_logic_vector(15 downto 0) := (others => '0');

  -- IF/ID
  signal if_id_valid : std_logic := '0';
  signal if_id_pc    : std_logic_vector(15 downto 0) := (others => '0');
  signal if_id_instr : std_logic_vector(31 downto 0) := (others => '0');

  -- ID/EX
  signal id_ex_valid   : std_logic := '0';
  signal id_ex_pc      : std_logic_vector(15 downto 0) := (others => '0');
  signal id_ex_instr   : std_logic_vector(31 downto 0) := (others => '0');
  signal id_ex_ctrl    : ctrl_t := CTRL_ZERO;
  signal id_ex_rs1_val : std_logic_vector(15 downto 0) := (others => '0');
  signal id_ex_rs2_val : std_logic_vector(15 downto 0) := (others => '0');

  -- EX/MEM
  signal ex_mem_valid     : std_logic := '0';
  signal ex_mem_pc        : std_logic_vector(15 downto 0) := (others => '0');
  signal ex_mem_instr     : std_logic_vector(31 downto 0) := (others => '0');
  signal ex_mem_ctrl      : ctrl_t := CTRL_ZERO;
  signal ex_mem_result    : std_logic_vector(15 downto 0) := (others => '0');
  signal ex_mem_store_data: std_logic_vector(15 downto 0) := (others => '0');
  signal ex_mem_addr      : std_logic_vector(15 downto 0) := (others => '0');
  signal ex_mem_taken     : std_logic := '0';
  signal ex_mem_target    : std_logic_vector(15 downto 0) := (others => '0');
  signal ex_mem_is_halt   : std_logic := '0';

  -- MEM/WB
  signal mem_wb_valid       : std_logic := '0';
  signal mem_wb_pc          : std_logic_vector(15 downto 0) := (others => '0');
  signal mem_wb_instr       : std_logic_vector(31 downto 0) := (others => '0');
  signal mem_wb_ctrl        : ctrl_t := CTRL_ZERO;
  signal mem_wb_result      : std_logic_vector(15 downto 0) := (others => '0');
  signal mem_wb_mem_addr    : std_logic_vector(15 downto 0) := (others => '0');
  signal mem_wb_mem_size    : std_logic_vector(1 downto 0) := (others => '0');
  signal mem_wb_store_data  : std_logic_vector(15 downto 0) := (others => '0');
  signal mem_wb_taken       : std_logic := '0';
  signal mem_wb_target      : std_logic_vector(15 downto 0) := (others => '0');
  signal mem_wb_is_halt     : std_logic := '0';
  signal mem_wb_mem_is_mmio : std_logic := '0';

  -- ID decode and operands
  signal id_ctrl   : ctrl_t := CTRL_ZERO;
  signal rf_rdata1 : std_logic_vector(15 downto 0);
  signal rf_rdata2 : std_logic_vector(15 downto 0);

  -- EX
  signal fwd_a, fwd_b   : std_logic_vector(1 downto 0);
  signal a_ex, b_ex     : std_logic_vector(15 downto 0);
  signal alu_b          : std_logic_vector(15 downto 0);
  signal alu_result     : std_logic_vector(15 downto 0);
  signal pc_plus_1      : std_logic_vector(15 downto 0);
  signal branch_target  : std_logic_vector(15 downto 0);
  signal jalr_target    : std_logic_vector(15 downto 0);
  signal target_ex      : std_logic_vector(15 downto 0);
  signal addr_ex        : std_logic_vector(15 downto 0);
  signal result_ex      : std_logic_vector(15 downto 0);
  signal branch_cond    : std_logic;
  signal ex_redirect    : std_logic;
  signal ex_halt        : std_logic;

  -- Hazards
  signal stall          : std_logic;
  signal cause_ex       : std_logic;
  signal cause_mem      : std_logic;
  signal cause_rs1      : std_logic;
  signal cause_rs2      : std_logic;
  signal cause_store    : std_logic;
  signal cause_load_use : std_logic;

  -- MEM
  signal mem_is_mmio    : std_logic;
  signal mem_load_en    : std_logic;
  signal mem_store_en   : std_logic;
  signal mem_load_type  : std_logic_vector(1 downto 0);
  signal mem_store_type : std_logic;
  signal dmem_rdata     : std_logic_vector(15 downto 0);
  signal mmio_rdata     : std_logic_vector(15 downto 0);
  signal mmio_rdata_q   : std_logic_vector(15 downto 0) := (others => '0');

  -- WB
  signal wb_value : std_logic_vector(15 downto 0);
  signal wb_we    : std_logic;

  -- MMIO
  signal timer_value     : std_logic_vector(15 downto 0);
  signal uart_byte_s     : std_logic_vector(7 downto 0);
  signal uart_valid_s    : std_logic;
  signal uart_busy_s     : std_logic;
  signal uart_drop_pulse : std_logic;
  signal gpio_out_s      : std_logic_vector(15 downto 0);
  signal mmio_reads      : std_logic_vector(31 downto 0);
  signal mmio_writes     : std_logic_vector(31 downto 0);
  signal mmio_drops      : std_logic_vector(31 downto 0);

  -- Cycle counter for TIMER (cycle 1 reads 1).
  signal cycle_count : unsigned(15 downto 0) := (others => '0');

  -- Verification counters.
  signal cov_reg : cov_t := (others => (others => '0'));

begin

  -- Elaboration-time parameter checks.
  assert INSTR_WORDS > 0 report "INSTR_WORDS must be positive" severity failure;
  assert RAM_BYTES > 0 and RAM_BYTES <= RAM_BYTES_MAX report "RAM_BYTES out of range" severity failure;
  assert (RAM_BYTES mod 2) = 0 report "RAM_BYTES must be even" severity failure;
  assert UART_CYCLES_PER_BYTE >= 0 report "UART_CYCLES_PER_BYTE must be >= 0" severity failure;

  pc_out <= std_logic_vector(pc);

  -- During a stall the imem output register is the IF/ID instruction register.
  -- Re-read the ID instruction's own address so the register holds it while the
  -- fetch pointer PC is frozen; re-reading PC would replace it with the younger
  -- instruction.  This is the only special case in the imem address path.
  imem_addr <= if_id_pc when stall = '1' else std_logic_vector(pc);
  uart_tx_byte  <= uart_byte_s;
  uart_tx_valid <= uart_valid_s;
  uart_busy     <= uart_busy_s;
  gpio_out      <= gpio_out_s;

  ---------------------------------------------------------------------------
  -- Instruction memory: its output register is the IF/ID instruction field.
  ---------------------------------------------------------------------------
  u_imem : entity work.imem
    generic map (
      INSTR_WORDS => INSTR_WORDS,
      INIT        => INSTR_INIT
    )
    port map (
      clk   => clk,
      rst   => rst,
      addr  => imem_addr,
      instr => if_id_instr
    );

  ---------------------------------------------------------------------------
  -- ID: decode and register file.
  ---------------------------------------------------------------------------
  u_decoder : entity work.decoder
    port map (
      instr => if_id_instr,
      ctrl  => id_ctrl
    );

  u_rf : entity work.register_file
    port map (
      clk    => clk,
      rst    => rst,
      raddr1 => id_ctrl.rs1,
      raddr2 => id_ctrl.rs2,
      rdata1 => rf_rdata1,
      rdata2 => rf_rdata2,
      we     => wb_we,
      waddr  => mem_wb_ctrl.rd,
      wdata  => wb_value
    );

  ---------------------------------------------------------------------------
  -- Hazard and forwarding control.
  ---------------------------------------------------------------------------
  u_hazard : entity work.hazard_unit
    generic map (FORWARDING => FORWARDING)
    port map (
      id_valid         => if_id_valid,
      id_src1_en       => id_ctrl.rs1_en,
      id_rs1           => id_ctrl.rs1,
      id_src2_en       => id_ctrl.rs2_en,
      id_rs2           => id_ctrl.rs2,
      id_is_store      => id_ctrl.is_store,
      ex_valid         => id_ex_valid,
      ex_reg_write     => id_ex_ctrl.reg_write,
      ex_rd            => id_ex_ctrl.rd,
      ex_is_load       => id_ex_ctrl.is_load,
      mem_valid        => ex_mem_valid,
      mem_reg_write    => ex_mem_ctrl.reg_write,
      mem_rd           => ex_mem_ctrl.rd,
      mem_is_load      => ex_mem_ctrl.is_load,
      redirect         => ex_redirect,
      halt_ex          => ex_halt,
      stall            => stall,
      cause_ex         => cause_ex,
      cause_mem        => cause_mem,
      cause_rs1        => cause_rs1,
      cause_rs2        => cause_rs2,
      cause_store_data => cause_store,
      cause_load_use   => cause_load_use
    );

  u_forward : entity work.forwarding_unit
    generic map (FORWARDING => FORWARDING)
    port map (
      id_rs1        => id_ex_ctrl.rs1,
      id_rs2        => id_ex_ctrl.rs2,
      ex_valid      => id_ex_valid,
      ex_reg_write  => id_ex_ctrl.reg_write,
      ex_rd         => id_ex_ctrl.rd,
      ex_is_load    => id_ex_ctrl.is_load,
      mem_valid     => ex_mem_valid,
      mem_reg_write => ex_mem_ctrl.reg_write,
      mem_rd        => ex_mem_ctrl.rd,
      sel_a         => fwd_a,
      sel_b         => fwd_b
    );

  ---------------------------------------------------------------------------
  -- EX: operand muxes, ALU, branch, targets, link.
  ---------------------------------------------------------------------------
  with fwd_a select a_ex <=
    id_ex_rs1_val  when FWD_ID,
    ex_mem_result  when FWD_EXMEM,
    wb_value       when FWD_MEMWB,
    id_ex_rs1_val  when others;

  with fwd_b select b_ex <=
    id_ex_rs2_val  when FWD_ID,
    ex_mem_result  when FWD_EXMEM,
    wb_value       when FWD_MEMWB,
    id_ex_rs2_val  when others;

  alu_b <= b_ex when id_ex_ctrl.use_imm = '0' else id_ex_ctrl.imm;

  u_alu : entity work.alu
    port map (
      op     => id_ex_ctrl.op,
      a      => a_ex,
      b      => alu_b,
      result => alu_result
    );

  u_branch : entity work.branch_unit
    port map (
      op    => id_ex_ctrl.op,
      a     => a_ex,
      b     => b_ex,
      taken => branch_cond
    );

  pc_plus_1     <= std_logic_vector(unsigned(id_ex_pc) + 1);
  branch_target <= std_logic_vector(unsigned(pc_plus_1) + unsigned(id_ex_ctrl.imm));
  jalr_target   <= std_logic_vector(unsigned(a_ex) + unsigned(id_ex_ctrl.imm));
  target_ex     <= jalr_target when id_ex_ctrl.is_jalr = '1' else branch_target;
  addr_ex       <= std_logic_vector(unsigned(a_ex) + unsigned(id_ex_ctrl.imm));

  ex_redirect <= id_ex_valid and
                 (id_ex_ctrl.is_jal or id_ex_ctrl.is_jalr or
                  (id_ex_ctrl.is_branch and branch_cond));
  ex_halt <= id_ex_valid and id_ex_ctrl.is_halt;

  result_ex <= pc_plus_1 when (id_ex_ctrl.is_jal = '1' or id_ex_ctrl.is_jalr = '1') else
               addr_ex   when (id_ex_ctrl.is_load = '1' or id_ex_ctrl.is_store = '1') else
               alu_result;

  ---------------------------------------------------------------------------
  -- MEM: data memory and MMIO.  A store commits at the end of MEM; a load is
  -- captured at the end of MEM.  dmem_rdata is the registered RAM load value;
  -- mmio_rdata_q is the registered MMIO load value.
  ---------------------------------------------------------------------------
  mem_is_mmio    <= '1' when ex_mem_addr(15 downto 8) = x"FF" else '0';
  mem_load_en    <= ex_mem_valid and ex_mem_ctrl.is_load and (not rst);
  mem_store_en   <= ex_mem_valid and ex_mem_ctrl.is_store and (not rst);
  mem_load_type  <= load_type_of(ex_mem_ctrl);
  mem_store_type <= ex_mem_ctrl.mem_size;

  u_dmem : entity work.dmem
    generic map (
      RAM_BYTES => RAM_BYTES,
      INIT      => DATA_INIT
    )
    port map (
      clk        => clk,
      rst        => rst,
      addr       => ex_mem_addr,
      load_en    => mem_load_en,
      load_type  => mem_load_type,
      store_en   => mem_store_en,
      store_type => mem_store_type,
      wdata      => ex_mem_store_data,
      rdata      => dmem_rdata
    );

  u_mmio : entity work.mmio
    generic map (
      UART_CYCLES_PER_BYTE => UART_CYCLES_PER_BYTE
    )
    port map (
      clk             => clk,
      rst             => rst,
      addr            => ex_mem_addr,
      load_en         => mem_load_en,
      load_type       => mem_load_type,
      store_en        => mem_store_en,
      store_type      => mem_store_type,
      wdata           => ex_mem_store_data,
      rdata           => mmio_rdata,
      gpio_in         => gpio_in,
      gpio_out        => gpio_out_s,
      timer_value     => timer_value,
      uart_tx_byte    => uart_byte_s,
      uart_tx_valid   => uart_valid_s,
      uart_busy       => uart_busy_s,
      uart_drop_pulse => uart_drop_pulse,
      read_count      => mmio_reads,
      write_count     => mmio_writes,
      drop_count      => mmio_drops
    );

  process (clk)
  begin
    if rising_edge(clk) then
      if rst = '1' then
        mmio_rdata_q <= (others => '0');
      elsif mem_load_en = '1' and mem_is_mmio = '1' then
        mmio_rdata_q <= mmio_rdata;
      end if;
    end if;
  end process;

  timer_value <= std_logic_vector(cycle_count + 1);

  ---------------------------------------------------------------------------
  -- WB and retirement.
  ---------------------------------------------------------------------------
  wb_value <= mmio_rdata_q when (mem_wb_ctrl.is_load = '1' and mem_wb_mem_is_mmio = '1') else
              dmem_rdata    when mem_wb_ctrl.is_load = '1' else
              mem_wb_result;

  wb_we <= '1' when (mem_wb_valid = '1' and mem_wb_ctrl.reg_write = '1' and
                     mem_wb_ctrl.rd /= "0000" and rst = '0') else '0';

  retire_valid     <= mem_wb_valid and (not rst);
  retire_pc        <= mem_wb_pc;
  retire_instr     <= mem_wb_instr;
  retire_reg_write <= wb_we;
  retire_rd        <= mem_wb_ctrl.rd;
  retire_value     <= wb_value;
  retire_mem_kind  <= "01" when mem_wb_ctrl.is_load = '1' else
                      "10" when mem_wb_ctrl.is_store = '1' else
                      "00";
  retire_mem_addr  <= mem_wb_mem_addr;
  retire_mem_size  <= mem_wb_mem_size;
  retire_mem_data  <= wb_value when mem_wb_ctrl.is_load = '1' else
                      mem_wb_store_data when mem_wb_ctrl.mem_size = '1' else
                      x"00" & mem_wb_store_data(7 downto 0);
  retire_taken     <= mem_wb_taken;
  retire_target    <= mem_wb_target;
  retire_halt      <= mem_wb_is_halt;

  ---------------------------------------------------------------------------
  -- Sequential pipeline.  Reset affects only pipeline/PC state and the
  -- explicitly specified I/O state (GPIO_OUT/TIMER/UART are reset in mmio).
  ---------------------------------------------------------------------------
  process (clk)
  begin
    if rising_edge(clk) then
      if rst = '1' then
        pc            <= (others => '0');
        stop_fetch    <= '0';
        if_id_valid   <= '0';
        if_id_pc      <= (others => '0');
        id_ex_valid   <= '0';
        id_ex_pc      <= (others => '0');
        id_ex_instr   <= (others => '0');
        id_ex_ctrl    <= CTRL_ZERO;
        id_ex_rs1_val <= (others => '0');
        id_ex_rs2_val <= (others => '0');
        ex_mem_valid  <= '0';
        ex_mem_pc     <= (others => '0');
        ex_mem_instr  <= (others => '0');
        ex_mem_ctrl   <= CTRL_ZERO;
        ex_mem_result <= (others => '0');
        ex_mem_store_data <= (others => '0');
        ex_mem_addr   <= (others => '0');
        ex_mem_taken  <= '0';
        ex_mem_target <= (others => '0');
        ex_mem_is_halt<= '0';
        mem_wb_valid  <= '0';
        mem_wb_pc     <= (others => '0');
        mem_wb_instr  <= (others => '0');
        mem_wb_ctrl   <= CTRL_ZERO;
        mem_wb_result <= (others => '0');
        mem_wb_mem_addr <= (others => '0');
        mem_wb_mem_size <= (others => '0');
        mem_wb_store_data <= (others => '0');
        mem_wb_taken  <= '0';
        mem_wb_target <= (others => '0');
        mem_wb_is_halt<= '0';
        mem_wb_mem_is_mmio <= '0';
        cycle_count   <= (others => '0');
      else
        cycle_count <= cycle_count + 1;

        -- PC update priority: redirect, HALT freeze, stall, stop_fetch, +1.
        if ex_redirect = '1' then
          pc <= unsigned(target_ex);
        elsif ex_halt = '1' then
          pc <= pc;
        elsif stall = '1' then
          pc <= pc;
        elsif stop_fetch = '1' then
          pc <= pc;
        else
          pc <= pc + 1;
        end if;

        if ex_halt = '1' then
          stop_fetch <= '1';
        end if;

        -- IF/ID.
        if ex_redirect = '1' or ex_halt = '1' then
          if_id_valid <= '0';
        elsif stall = '1' then
          if_id_valid <= if_id_valid;
        elsif stop_fetch = '1' then
          if_id_valid <= '0';
        else
          if_id_valid <= '1';
          if_id_pc    <= std_logic_vector(pc);
        end if;

        -- ID/EX: bubble on flush or stall.
        if ex_redirect = '1' or ex_halt = '1' or stall = '1' then
          id_ex_valid <= '0';
        else
          id_ex_valid   <= if_id_valid;
          id_ex_pc      <= if_id_pc;
          id_ex_instr   <= if_id_instr;
          id_ex_ctrl    <= id_ctrl;
          id_ex_rs1_val <= rf_rdata1;
          id_ex_rs2_val <= rf_rdata2;
        end if;

        -- EX/MEM.
        ex_mem_valid      <= id_ex_valid;
        ex_mem_pc         <= id_ex_pc;
        ex_mem_instr      <= id_ex_instr;
        ex_mem_ctrl       <= id_ex_ctrl;
        ex_mem_result     <= result_ex;
        ex_mem_store_data <= b_ex;
        ex_mem_addr       <= addr_ex;
        ex_mem_taken      <= ex_redirect;
        ex_mem_target     <= target_ex;
        ex_mem_is_halt    <= id_ex_ctrl.is_halt;

        -- MEM/WB.
        mem_wb_valid     <= ex_mem_valid;
        mem_wb_pc        <= ex_mem_pc;
        mem_wb_instr     <= ex_mem_instr;
        mem_wb_ctrl      <= ex_mem_ctrl;
        mem_wb_result    <= ex_mem_result;
        mem_wb_store_data<= ex_mem_store_data;
        mem_wb_taken     <= ex_mem_taken;
        mem_wb_target    <= ex_mem_target;
        mem_wb_is_halt   <= ex_mem_is_halt;
        mem_wb_mem_is_mmio <= mem_is_mmio;

        if (ex_mem_ctrl.is_load = '1' or ex_mem_ctrl.is_store = '1') then
          if ex_mem_ctrl.mem_size = '1' then
            mem_wb_mem_addr <= ex_mem_addr and x"FFFE";
            mem_wb_mem_size <= "10";
          else
            mem_wb_mem_addr <= ex_mem_addr;
            mem_wb_mem_size <= "01";
          end if;
        else
          mem_wb_mem_addr <= ex_mem_addr;
          mem_wb_mem_size <= "00";
        end if;
      end if;
    end if;
  end process;

  ---------------------------------------------------------------------------
  -- Optional verification counters.  They observe signals and never feed the
  -- architectural datapath.
  ---------------------------------------------------------------------------
  process (clk)
  begin
    if rising_edge(clk) then
      if rst = '1' then
        cov_reg <= (others => (others => '0'));
      elsif COUNTING then
        if stall = '1' then
          cov_reg(COV_STALL_TOTAL) <= std_logic_vector(unsigned(cov_reg(COV_STALL_TOTAL)) + 1);
        end if;
        if cause_ex = '1' then
          cov_reg(COV_STALL_EX) <= std_logic_vector(unsigned(cov_reg(COV_STALL_EX)) + 1);
        end if;
        if cause_mem = '1' then
          cov_reg(COV_STALL_MEM) <= std_logic_vector(unsigned(cov_reg(COV_STALL_MEM)) + 1);
        end if;
        if cause_rs1 = '1' then
          cov_reg(COV_STALL_RS1) <= std_logic_vector(unsigned(cov_reg(COV_STALL_RS1)) + 1);
        end if;
        if cause_rs2 = '1' then
          cov_reg(COV_STALL_RS2) <= std_logic_vector(unsigned(cov_reg(COV_STALL_RS2)) + 1);
        end if;
        if cause_store = '1' then
          cov_reg(COV_STALL_STORE_DATA) <= std_logic_vector(unsigned(cov_reg(COV_STALL_STORE_DATA)) + 1);
        end if;
        if cause_load_use = '1' then
          cov_reg(COV_STALL_LOAD_USE) <= std_logic_vector(unsigned(cov_reg(COV_STALL_LOAD_USE)) + 1);
        end if;
        if ex_redirect = '1' or ex_halt = '1' then
          cov_reg(COV_FLUSH) <= std_logic_vector(unsigned(cov_reg(COV_FLUSH)) + 1);
        end if;
        if ex_redirect = '1' and id_ex_ctrl.is_branch = '1' and branch_cond = '1' then
          cov_reg(COV_FLUSH_BRANCH) <= std_logic_vector(unsigned(cov_reg(COV_FLUSH_BRANCH)) + 1);
        end if;
        if ex_redirect = '1' and (id_ex_ctrl.is_jal = '1' or id_ex_ctrl.is_jalr = '1') then
          cov_reg(COV_FLUSH_JUMP) <= std_logic_vector(unsigned(cov_reg(COV_FLUSH_JUMP)) + 1);
        end if;
        if ex_redirect = '1' and if_id_valid = '1' and id_ctrl.is_halt = '1' then
          cov_reg(COV_HALT_SHADOW) <= std_logic_vector(unsigned(cov_reg(COV_HALT_SHADOW)) + 1);
        end if;
        if mem_wb_valid = '1' and mem_wb_ctrl.is_load = '1' and mem_wb_ctrl.rd = "0000" then
          cov_reg(COV_LOAD_R0) <= std_logic_vector(unsigned(cov_reg(COV_LOAD_R0)) + 1);
        end if;
        if uart_drop_pulse = '1' then
          cov_reg(COV_UART_DROP) <= std_logic_vector(unsigned(cov_reg(COV_UART_DROP)) + 1);
        end if;
        if ex_mem_valid = '1' and
           (ex_mem_ctrl.is_load = '1' or ex_mem_ctrl.is_store = '1') and
           ex_mem_ctrl.mem_size = '1' and ex_mem_addr(0) = '1' then
          cov_reg(COV_MISALIGNED) <= std_logic_vector(unsigned(cov_reg(COV_MISALIGNED)) + 1);
        end if;
        if ex_mem_valid = '1' and mem_is_mmio = '1' then
          if ex_mem_ctrl.is_load = '1' then
            cov_reg(COV_MMIO_READ) <= std_logic_vector(unsigned(cov_reg(COV_MMIO_READ)) + 1);
          end if;
          if ex_mem_ctrl.is_store = '1' then
            cov_reg(COV_MMIO_WRITE) <= std_logic_vector(unsigned(cov_reg(COV_MMIO_WRITE)) + 1);
          end if;
        end if;
      end if;
    end if;
  end process;

  cov <= cov_reg when COUNTING else (others => (others => '0'));

end architecture rtl;
