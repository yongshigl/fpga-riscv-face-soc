`include "src/vision_config.vh"
`timescale 1ns/1ps
`define CPU_MEMORY_HANDSHAKE
`define CPU_EARLY_LOAD_PORTS

module rv32i_pipeline_core #(
    parameter integer WAIT_MEMORY = 0,
    parameter integer FETCH_PIPE_STAGES =
`ifdef CPU_FETCH_PIPE_STAGES
        `CPU_FETCH_PIPE_STAGES,
`else
        0,
`endif
    parameter integer DECOUPLE_FETCH =
`ifdef CPU_DECOUPLE_FETCH
        `CPU_DECOUPLE_FETCH,
`else
        0,
`endif
    parameter integer MULTIPLY_PIPELINE =
`ifdef CPU_MULTIPLY_PIPELINE
        `CPU_MULTIPLY_PIPELINE,
`else
        0,
`endif
    parameter integer BTB_INDEX_BITS = 5,
    parameter integer RAS_BITS =
`ifdef CPU_RAS_BITS
        `CPU_RAS_BITS,
`else
        0,
`endif
    parameter integer LOAD_USE_GUARD =
`ifdef CPU_LOAD_USE_GUARD
        `CPU_LOAD_USE_GUARD,
`else
        0,
`endif
    parameter integer CONTROL_LOAD_GUARD =
`ifdef CPU_CONTROL_LOAD_GUARD
        `CPU_CONTROL_LOAD_GUARD,
`else
        0,
`endif
    parameter integer REGISTER_BYPASS =
`ifdef CPU_REGISTER_BYPASS
        `CPU_REGISTER_BYPASS,
`else
        0,
`endif
    parameter integer COUNTER_CSR_PREDECODE =
`ifdef CPU_COUNTER_CSR_PREDECODE
        `CPU_COUNTER_CSR_PREDECODE,
`else
        0,
`endif
    parameter integer CSR_ADDRESS_PREDECODE =
`ifdef CPU_CSR_ADDRESS_PREDECODE
        `CPU_CSR_ADDRESS_PREDECODE,
`else
        0,
`endif
    parameter integer EARLY_LOAD_REQUEST =
`ifdef CPU_EARLY_LOAD_REQUEST
        `CPU_EARLY_LOAD_REQUEST,
`else
        0,
`endif
    parameter integer SEPARATE_EXMEM_PAYLOAD =
`ifdef CPU_SEPARATE_EXMEM_PAYLOAD
        `CPU_SEPARATE_EXMEM_PAYLOAD,
`else
        0,
`endif
    parameter integer SEPARATE_FRONTEND_PAYLOAD =
`ifdef CPU_SEPARATE_FRONTEND_PAYLOAD
        `CPU_SEPARATE_FRONTEND_PAYLOAD,
`else
        0,
`endif
    parameter integer EARLY_BRANCH_CHECK =
`ifdef CPU_EARLY_BRANCH_CHECK
        `CPU_EARLY_BRANCH_CHECK,
`else
        0,
`endif
    parameter integer FAST_REDIRECT_SELECT =
`ifdef CPU_FAST_REDIRECT_SELECT
        `CPU_FAST_REDIRECT_SELECT,
`else
        0,
`endif
    parameter integer FAST_TRAP_PATH =
`ifdef CPU_FAST_TRAP_PATH
        `CPU_FAST_TRAP_PATH
`else
        0
`endif
) (
    input  wire        clk,
    input  wire        rst_n,
    output wire        imem_valid,
    output wire [31:0] imem_addr,
    input  wire [31:0] imem_rdata,
    input  wire        imem_req_ready,
    input  wire        imem_ready,
    input  wire        imem_error,
    output wire        imem_consume,
    output wire        fetch_flush,
    output wire        fence_i,
    output wire        dmem_valid,
    output wire        dmem_we,
    output wire [31:0] dmem_addr,
    output wire [31:0] dmem_wdata,
    output wire [3:0]  dmem_wstrb,
    input  wire [31:0] dmem_rdata,
    input  wire        dmem_ready,
    input  wire        dmem_error,
    output wire        dmem_early_valid,
    output wire [31:0] dmem_early_addr,
    input  wire        irq_timer,
    input  wire        irq_external,
    output wire [31:0] current_pc,
    output wire [31:0] debug_x10,
    output reg         trap_taken,
    output wire [63:0] cycle_count,
    output wire [63:0] instret_count
);

    localparam [31:0] CAUSE_INST_MISALIGNED  = 32'd0;
    localparam [31:0] CAUSE_INST_ACCESS      = 32'd1;
    localparam [31:0] CAUSE_ILLEGAL          = 32'd2;
    localparam [31:0] CAUSE_BREAKPOINT       = 32'd3;
    localparam [31:0] CAUSE_LOAD_MISALIGNED  = 32'd4;
    localparam [31:0] CAUSE_LOAD_ACCESS      = 32'd5;
    localparam [31:0] CAUSE_STORE_MISALIGNED = 32'd6;
    localparam [31:0] CAUSE_STORE_ACCESS     = 32'd7;
    localparam [31:0] CAUSE_ECALL_M          = 32'd11;
    localparam [31:0] CAUSE_TIMER_IRQ        = 32'h80000007;
    localparam [31:0] CAUSE_EXTERNAL_IRQ     = 32'h8000000b;
    // Single-hart, machine-mode-only implementation: no U/S modes, floating
    // point state, software interrupt source or hardware division.
    localparam [31:0] MSTATUS_WRITABLE_MASK  = 32'h00000088; // MIE, MPIE
    localparam [31:0] MSTATUS_MPP_MACHINE   = 32'h00001800;
    localparam [31:0] MIE_WRITABLE_MASK      = 32'h00000880; // MEIE, MTIE
    // MXL=RV32 and I.  The multiply-only subset is not the complete M extension.
    localparam [31:0] MISA_FIXED            = 32'h40000100;

`ifdef CPU_REGFILE_LUTRAM
    reg [31:0] regs [0:31] /* synthesis syn_ramstyle="lut_ram" */;
`else
    reg [31:0] regs [0:31];
`endif
    reg [31:0] regs_valid;
    integer index;

    reg [31:0] fetch_pc;
    reg        fetch_pending, fetch_killed;
    reg [31:0] fetch_request_pc, fetch_request_pred_pc;
    reg        ifid_valid;
    reg [31:0] ifid_pc;
    reg [31:0] ifid_instr;
    reg [31:0] ifid_pred_pc;
    reg        ifid_access_error;

    reg        idex_valid;
    reg [31:0] idex_pc;
    reg [31:0] idex_instr;
    reg [31:0] idex_pred_pc;
    reg idex_branch_target_miss, idex_sequential_miss;
    reg [31:0] idex_jalr_expected0, idex_jalr_expected1;
    reg        idex_access_error;
    reg [4:0]  idex_rs1;
    reg [4:0]  idex_rs2;
    reg [4:0]  idex_rd;
    reg [31:0] idex_rs1_value;
    reg [31:0] idex_rs2_value;
    reg idex_exmem_rs1_match, idex_exmem_rs2_match;
    reg idex_memwb_rs1_match, idex_memwb_rs2_match;
    reg [3:0] idex_counter_csr_writes;
    reg idex_csr_address_valid;

    reg        exmem_valid;
    reg [31:0] exmem_pc;
    reg [4:0]  exmem_rd;
    reg        exmem_reg_write;
    reg        exmem_mem_read;
    reg        exmem_mem_write;
    reg [2:0]  exmem_funct3;
    reg [31:0] exmem_result;
    reg        exmem_mul_high;
    reg [31:0] exmem_mul_rs1;
    reg [31:0] exmem_mul_rs2;
    reg [31:0] exmem_store_data;
    reg [3:0]  exmem_store_strobe;

    reg        memwb_valid;
    reg [4:0]  memwb_rd;
    reg        memwb_reg_write;
    reg [31:0] memwb_data;

    reg [31:0] csr_mstatus;
    reg [31:0] csr_mie;
    reg [31:0] csr_mtvec;
    reg [31:0] csr_mscratch;
    reg [31:0] csr_mepc;
    reg [31:0] csr_mcause;
    reg [31:0] csr_mtval;
    reg [63:0] csr_mcycle;
    reg [63:0] csr_minstret;

    function [63:0] increment_counter;
        input [63:0] value;
        begin
            increment_counter[15:0] = value[15:0] + 16'd1;
            increment_counter[31:16] = value[31:16] + {15'd0, &value[15:0]};
            increment_counter[47:32] = value[47:32] + {15'd0, &value[31:0]};
            increment_counter[63:48] = value[63:48] + {15'd0, &value[47:0]};
        end
    endfunction

    localparam BTB_ENTRIES = 1 << BTB_INDEX_BITS;
    reg [BTB_ENTRIES-1:0] btb_valid;
    reg [31-BTB_INDEX_BITS-2:0] btb_tag [0:BTB_ENTRIES-1];
    reg [31:0] btb_target [0:BTB_ENTRIES-1];
    reg [1:0]  btb_counter [0:BTB_ENTRIES-1];
    reg [BTB_ENTRIES-1:0] btb_return;
    wire [BTB_INDEX_BITS-1:0] fetch_btb_index = fetch_pc[BTB_INDEX_BITS+1:2];
    wire [BTB_INDEX_BITS-1:0] ex_btb_index = idex_pc[BTB_INDEX_BITS+1:2];
    wire fetch_btb_hit = btb_valid[fetch_btb_index] &&
                         btb_tag[fetch_btb_index] == fetch_pc[31:BTB_INDEX_BITS+2];
    localparam RAS_POINTER_BITS = RAS_BITS == 0 ? 1 : RAS_BITS;
    localparam RAS_ENTRIES = 1 << RAS_POINTER_BITS;
    reg [31:0] ras_data [0:RAS_ENTRIES-1];
    reg [RAS_POINTER_BITS-1:0] ras_pointer;
    reg [RAS_POINTER_BITS:0] ras_count;
    wire [RAS_POINTER_BITS-1:0] ras_top_index = ras_pointer - 1'b1;
    wire fetch_return = fetch_btb_hit && btb_return[fetch_btb_index];
    wire [31:0] fetch_next_pc = (RAS_BITS != 0 && fetch_return && ras_count != 0) ?
                                ras_data[ras_top_index] :
                                (fetch_btb_hit && btb_counter[fetch_btb_index][1]) ?
                                btb_target[fetch_btb_index] : fetch_pc + 32'd4;
    wire ex_btb_hit = btb_valid[ex_btb_index] &&
                      btb_tag[ex_btb_index] == idex_pc[31:BTB_INDEX_BITS+2];

    wire [6:0] ifid_opcode = ifid_instr[6:0];
    wire [4:0] ifid_rs1 = ifid_instr[19:15];
    wire [4:0] ifid_rs2 = ifid_instr[24:20];
    wire [4:0] ifid_rd = ifid_instr[11:7];
    wire ifid_csr_operation = ifid_instr[14:12] == 3'b001 || ifid_instr[14:12] == 3'b010 ||
                             ifid_instr[14:12] == 3'b011 || ifid_instr[14:12] == 3'b101 ||
                             ifid_instr[14:12] == 3'b110 || ifid_instr[14:12] == 3'b111;
    wire ifid_counter_write = ifid_opcode == 7'b1110011 && ifid_csr_operation &&
                             (ifid_instr[13:12] == 2'b01 || ifid_rs1 != 0);
    // Address legality depends only on the instruction, so the ID stage can
    // resolve the 12-bit implemented-CSR decode before the EX trap controls.
    // CSR read data and architectural writes still use current EX-stage state.
    reg ifid_csr_address_valid;
    always @* begin
        case (ifid_instr[31:20])
            12'h300, 12'h301, 12'h304, 12'h305,
            12'h340, 12'h341, 12'h342, 12'h343, 12'h344,
            12'hf11, 12'hf12, 12'hf13, 12'hf14,
            12'hb00, 12'hb80, 12'hb02, 12'hb82,
            12'hc00, 12'hc80, 12'hc02, 12'hc82: ifid_csr_address_valid = 1'b1;
            default: ifid_csr_address_valid = 1'b0;
        endcase
    end
    // Four early address/write-intent decodes replace the late generic CSR
    // commit/address mux at the counter registers; architectural commit is EX.
    wire [3:0] ifid_counter_csr_writes = {
        ifid_counter_write && ifid_instr[31:20] == 12'hb82,
        ifid_counter_write && ifid_instr[31:20] == 12'hb02,
        ifid_counter_write && ifid_instr[31:20] == 12'hb80,
        ifid_counter_write && ifid_instr[31:20] == 12'hb00
    };
    wire [31:0] ifid_branch_immediate = {{19{ifid_instr[31]}}, ifid_instr[31],
        ifid_instr[7], ifid_instr[30:25], ifid_instr[11:8], 1'b0};
    wire ifid_branch_target_miss = ifid_pred_pc != ifid_pc + ifid_branch_immediate;
    wire ifid_sequential_miss = ifid_pred_pc != ifid_pc + 32'd4;
    wire [31:0] ifid_jalr_immediate = {{20{ifid_instr[31]}}, ifid_instr[31:20]};
    wire [31:0] ifid_jalr_expected0 = ifid_pred_pc - ifid_jalr_immediate;
    wire [31:0] ifid_jalr_expected1 = (ifid_pred_pc + 32'd1) - ifid_jalr_immediate;

    reg ifid_uses_rs1;
    reg ifid_uses_rs2;
    always @* begin
        ifid_uses_rs1 = 1'b0;
        ifid_uses_rs2 = 1'b0;
        case (ifid_opcode)
            7'b1100111,
            7'b0000011,
            7'b0010011: ifid_uses_rs1 = 1'b1;
            7'b1100011,
            7'b0100011,
            7'b0110011: begin
                ifid_uses_rs1 = 1'b1;
                ifid_uses_rs2 = 1'b1;
            end
            7'b1110011: begin
                if (ifid_instr[14:12] == 3'b001 ||
                    ifid_instr[14:12] == 3'b010 ||
                    ifid_instr[14:12] == 3'b011)
                    ifid_uses_rs1 = 1'b1;
            end
            default: begin
                ifid_uses_rs1 = 1'b0;
                ifid_uses_rs2 = 1'b0;
            end
        endcase
    end

    wire idex_mul = idex_valid && idex_instr[6:0] == 7'b0110011 &&
                    idex_instr[31:25] == 7'b0000001 && idex_instr[14:12] <= 3'b011;
    wire idex_mul_high = idex_mul &&
                         idex_instr[31:25] == 7'b0000001 &&
                         (MULTIPLY_PIPELINE != 0 || idex_instr[14:12] == 3'b001 ||
                          idex_instr[14:12] == 3'b010 ||
                          idex_instr[14:12] == 3'b011);
    wire ifid_mul = ifid_instr[6:0] == 7'b0110011 &&
                    ifid_instr[31:25] == 7'b0000001 &&
                    ifid_instr[14:12] <= 3'b011;
    wire ifid_control = ifid_opcode == 7'b1100011 || ifid_opcode == 7'b1100111;
    wire dependent_next = ifid_valid && idex_rd != 5'd0 &&
                          ((ifid_uses_rs1 && ifid_rs1 == idex_rd) ||
                           (ifid_uses_rs2 && ifid_rs2 == idex_rd));
    // A high-half product finishes in MEM. A load cannot feed the separate
    // multiplier operand path directly, so either dependency pauses once.
    wire load_use_stall = dependent_next &&
                          (idex_mul_high || (idex_valid && idex_instr[6:0] == 7'b0000011 &&
                           (LOAD_USE_GUARD != 0 || ifid_mul ||
                            (CONTROL_LOAD_GUARD != 0 && ifid_control))));
    reg [31:0] mem_load_data;

    wire [31:0] id_rs1_value = (ifid_rs1 == 5'd0) ? 32'd0 :
                               (memwb_valid && memwb_reg_write && memwb_rd == ifid_rs1) ? memwb_data :
                               (regs_valid[ifid_rs1] ? regs[ifid_rs1] : 32'd0);
    wire [31:0] id_rs2_value = (ifid_rs2 == 5'd0) ? 32'd0 :
                               (memwb_valid && memwb_reg_write && memwb_rd == ifid_rs2) ? memwb_data :
                               (regs_valid[ifid_rs2] ? regs[ifid_rs2] : 32'd0);

    wire exmem_rs1_match = REGISTER_BYPASS != 0 ? idex_exmem_rs1_match : exmem_rd == idex_rs1;
    wire exmem_rs2_match = REGISTER_BYPASS != 0 ? idex_exmem_rs2_match : exmem_rd == idex_rs2;
    wire memwb_rs1_match = REGISTER_BYPASS != 0 ? idex_memwb_rs1_match : memwb_rd == idex_rs1;
    wire memwb_rs2_match = REGISTER_BYPASS != 0 ? idex_memwb_rs2_match : memwb_rd == idex_rs2;

    wire [31:0] ex_rs1_value = (idex_rs1 == 5'd0) ? 32'd0 :
                               (exmem_valid && exmem_reg_write && !exmem_mul_high &&
                                (LOAD_USE_GUARD == 0 || !exmem_mem_read) && exmem_rs1_match) ?
                               (exmem_mem_read ? mem_load_data : exmem_result) :
                               (memwb_valid && memwb_reg_write && memwb_rs1_match) ? memwb_data :
                               idex_rs1_value;
    wire [31:0] ex_rs2_value = (idex_rs2 == 5'd0) ? 32'd0 :
                               (exmem_valid && exmem_reg_write && !exmem_mul_high &&
                                (LOAD_USE_GUARD == 0 || !exmem_mem_read) && exmem_rs2_match) ?
                               (exmem_mem_read ? mem_load_data : exmem_result) :
                               (memwb_valid && memwb_reg_write && memwb_rs2_match) ? memwb_data :
                               idex_rs2_value;
    wire [31:0] mul_rs1_value = (idex_rs1 == 5'd0) ? 32'd0 :
                                (exmem_valid && exmem_reg_write && !exmem_mem_read &&
                                 !exmem_mul_high && exmem_rs1_match) ? exmem_result :
                                (memwb_valid && memwb_reg_write && memwb_rs1_match) ? memwb_data :
                                idex_rs1_value;
    wire [31:0] mul_rs2_value = (idex_rs2 == 5'd0) ? 32'd0 :
                                (exmem_valid && exmem_reg_write && !exmem_mem_read &&
                                 !exmem_mul_high && exmem_rs2_match) ? exmem_result :
                                (memwb_valid && memwb_reg_write && memwb_rs2_match) ? memwb_data :
                                idex_rs2_value;
    wire [31:0] control_rs1_value = CONTROL_LOAD_GUARD != 0 ? mul_rs1_value : ex_rs1_value;
    wire [31:0] control_rs2_value = CONTROL_LOAD_GUARD != 0 ? mul_rs2_value : ex_rs2_value;
    // Parallel byte comparisons shorten the serial EX branch comparator.
    (* keep = "true" *) wire [3:0] branch_byte_equal, branch_byte_less;
    genvar branch_byte;
    generate for (branch_byte = 0; branch_byte < 4; branch_byte = branch_byte + 1) begin : branch_compare
        assign branch_byte_equal[branch_byte] =
            control_rs1_value[branch_byte*8 +: 8] == control_rs2_value[branch_byte*8 +: 8];
        assign branch_byte_less[branch_byte] =
            control_rs1_value[branch_byte*8 +: 8] < control_rs2_value[branch_byte*8 +: 8];
    end endgenerate
    wire branch_fast_equal = &branch_byte_equal;
    wire branch_fast_less = branch_byte_less[3] ||
        (branch_byte_equal[3] && branch_byte_less[2]) ||
        (branch_byte_equal[3] && branch_byte_equal[2] && branch_byte_less[1]) ||
        (branch_byte_equal[3] && branch_byte_equal[2] && branch_byte_equal[1] && branch_byte_less[0]);
    wire branch_equal = FAST_TRAP_PATH != 0 ? branch_fast_equal : control_rs1_value == control_rs2_value;
    wire branch_less_unsigned = FAST_TRAP_PATH != 0 ? branch_fast_less : control_rs1_value < control_rs2_value;
    wire branch_less_signed = FAST_TRAP_PATH != 0 ?
        (control_rs1_value[31] != control_rs2_value[31] ? control_rs1_value[31] : branch_fast_less) :
        $signed(control_rs1_value) < $signed(control_rs2_value);
    // A legal branch selects EQ / signed LT / unsigned LT, then its inverse.
    // Invalid funct3 values still trap in EX before any recovery PC can commit.
    (* keep = "true" *) wire branch_condition_raw =
        (idex_instr[14] ? (idex_instr[13] ? branch_less_unsigned : branch_less_signed)
                            : branch_equal) ^ idex_instr[12];

    wire [31:0] csr_mip = {20'd0, irq_external, 3'd0, irq_timer, 7'd0};
    wire interrupt_enabled = csr_mstatus[3] &&
                             ((irq_external && csr_mie[11]) || (irq_timer && csr_mie[7]));
    wire memory_stall = WAIT_MEMORY != 0 && dmem_valid && !dmem_ready;
    wire memory_fault = WAIT_MEMORY != 0 && dmem_valid && dmem_ready && dmem_error;
    wire execute_advance = !memory_stall && !memory_fault;
    wire take_interrupt = idex_valid && interrupt_enabled && !ex_exception && execute_advance;
    wire [31:0] interrupt_cause = (irq_external && csr_mie[11]) ? CAUSE_EXTERNAL_IRQ : CAUSE_TIMER_IRQ;

    reg [31:0] csr_read_data;
    reg csr_address_valid_ex;
    wire csr_address_valid = CSR_ADDRESS_PREDECODE != 0 ? idex_csr_address_valid : csr_address_valid_ex;
    always @* begin
        csr_address_valid_ex = 1'b1;
        case (idex_instr[31:20])
            12'h300: csr_read_data = csr_mstatus;
            12'h301: csr_read_data = MISA_FIXED;
            12'h304: csr_read_data = csr_mie;
            12'h305: csr_read_data = csr_mtvec;
            12'h340: csr_read_data = csr_mscratch;
            12'h341: csr_read_data = csr_mepc;
            12'h342: csr_read_data = csr_mcause;
            12'h343: csr_read_data = csr_mtval;
            12'h344: csr_read_data = csr_mip;
            // Zero denotes no assigned vendor/architecture/implementation ID;
            // the only implemented hart has ID zero.  These addresses are RO.
            12'hf11, 12'hf12, 12'hf13, 12'hf14: csr_read_data = 32'd0;
            12'hb00,
            12'hc00: csr_read_data = csr_mcycle[31:0];
            12'hb80,
            12'hc80: csr_read_data = csr_mcycle[63:32];
            12'hb02,
            12'hc02: csr_read_data = csr_minstret[31:0];
            12'hb82,
            12'hc82: csr_read_data = csr_minstret[63:32];
            default: begin
                csr_read_data = 32'd0;
                csr_address_valid_ex = 1'b0;
            end
        endcase
    end

    reg        ex_reg_write;
    reg        ex_mem_read;
    reg        ex_mem_write;
    reg [2:0]  ex_mem_funct3;
    reg [31:0] ex_result;
    reg [31:0] ex_store_data;
    reg [3:0]  ex_store_strobe;
    reg        ex_redirect;
    reg [31:0] ex_redirect_pc;
    reg        ex_exception;
    reg [31:0] ex_exception_cause;
    reg [31:0] ex_exception_tval;
    reg        ex_csr_write;
    reg [11:0] ex_csr_addr;
    reg [31:0] ex_csr_wdata;
    reg        ex_mret;

    reg [31:0] ex_imm_i;
    reg [31:0] ex_imm_s;
    reg [31:0] ex_imm_b;
    reg [31:0] ex_imm_u;
    reg [31:0] ex_imm_j;
    reg [31:0] ex_address;
    reg [31:0] ex_target;
    reg        ex_branch_taken;
    reg        ex_legal;
    reg [31:0] csr_operand;
    reg        csr_is_read_only;
    // CSRRS/CSRRC suppress writes only when the encoded source is x0;
    // a different register containing zero still requests a write.  The
    // same field is zimm for CSRRSI/CSRRCI.  CSRRW/CSRRWI always write.
    wire csr_write_requested = idex_instr[13:12] == 2'b01 || idex_instr[19:15] != 0;
    wire [63:0] mul_uu = {32'd0, mul_rs1_value} * {32'd0, mul_rs2_value};
    wire mul_signed_a = idex_instr[14:12] == 3'b001 || idex_instr[14:12] == 3'b010;
    wire mul_signed_b = idex_instr[14:12] == 3'b001;
    wire signed [16:0] mul_low_a = {1'b0, mul_rs1_value[15:0]};
    wire signed [16:0] mul_low_b = {1'b0, mul_rs2_value[15:0]};
    wire signed [16:0] mul_high_a = {mul_signed_a && mul_rs1_value[31], mul_rs1_value[31:16]};
    wire signed [16:0] mul_high_b = {mul_signed_b && mul_rs2_value[31], mul_rs2_value[31:16]};
    reg signed [33:0] mul_ll, mul_lh, mul_hl, mul_hh;
    wire signed [34:0] mul_middle = $signed({19'd0, mul_ll[31:16]}) +
                                    $signed({mul_lh[33], mul_lh}) + $signed({mul_hl[33], mul_hl});
    wire signed [34:0] mul_upper = $signed({mul_hh[33], mul_hh}) + (mul_middle >>> 16);
    wire [31:0] pipelined_mul_result = exmem_funct3 == 0 ?
                                       {mul_middle[15:0], mul_ll[15:0]} : mul_upper[31:0];
    wire [31:0] mem_mul_high_su = exmem_result -
                                  (exmem_mul_rs1[31] ? exmem_mul_rs2 : 32'd0);
    wire [31:0] mem_mul_high_ss = mem_mul_high_su -
                                  (exmem_mul_rs2[31] ? exmem_mul_rs1 : 32'd0);
    wire [31:0] mem_mul_high_result = MULTIPLY_PIPELINE != 0 ? pipelined_mul_result :
                                      exmem_funct3 == 3'b001 ? mem_mul_high_ss :
                                      exmem_funct3 == 3'b010 ? mem_mul_high_su : exmem_result;

    wire ex_alu_register = idex_instr[6:0] == 7'b0110011;
    wire [31:0] ex_alu_b = ex_alu_register ? ex_rs2_value :
                           idex_instr[6:0] == 7'b0100011 ? ex_imm_s : ex_imm_i;
    wire ex_alu_subtract = (idex_instr[6:0] == 7'b0110011 || idex_instr[6:0] == 7'b0010011) &&
                           (idex_instr[14:12] == 3'b010 || idex_instr[14:12] == 3'b011 ||
                            (ex_alu_register && idex_instr[14:12] == 0 && idex_instr[30]));
    wire [32:0] ex_alu_sum = {1'b0, ex_rs1_value} +
                             {1'b0, (ex_alu_b ^ {32{ex_alu_subtract}})} + ex_alu_subtract;
    wire [31:0] ex_load_address = EARLY_LOAD_REQUEST != 0 ?
        ex_rs1_value + ex_imm_i : ex_alu_sum[31:0];
    wire ex_alu_less_unsigned = !ex_alu_sum[32];
    wire ex_alu_less_signed = ex_rs1_value[31] != ex_alu_b[31] ?
                              ex_rs1_value[31] : ex_alu_less_unsigned;
    wire [4:0] ex_shift_amount = ex_alu_register ? ex_rs2_value[4:0] : idex_instr[24:20];
    wire ex_shift_left = idex_instr[14:12] == 3'b001;
    wire [31:0] ex_shift_input, ex_shift_result;
    wire signed [32:0] ex_shifted =
        $signed({(!ex_shift_left && idex_instr[30] && ex_rs1_value[31]), ex_shift_input}) >>> ex_shift_amount;
    genvar shift_bit;
    // Reverse bits around one right shifter to implement all RV32I shifts.
    generate for (shift_bit = 0; shift_bit < 32; shift_bit = shift_bit + 1) begin : shared_shifter
        assign ex_shift_input[shift_bit] = ex_shift_left ? ex_rs1_value[31-shift_bit] : ex_rs1_value[shift_bit];
        assign ex_shift_result[shift_bit] = ex_shift_left ? ex_shifted[31-shift_bit] : ex_shifted[shift_bit];
    end endgenerate

    always @* begin
        ex_imm_i = {{20{idex_instr[31]}}, idex_instr[31:20]};
        ex_imm_s = {{20{idex_instr[31]}}, idex_instr[31:25], idex_instr[11:7]};
        ex_imm_b = {{19{idex_instr[31]}}, idex_instr[31], idex_instr[7], idex_instr[30:25], idex_instr[11:8], 1'b0};
        ex_imm_u = {idex_instr[31:12], 12'b0};
        ex_imm_j = {{11{idex_instr[31]}}, idex_instr[31], idex_instr[19:12], idex_instr[20], idex_instr[30:21], 1'b0};

        ex_reg_write = 1'b0;
        ex_mem_read = 1'b0;
        ex_mem_write = 1'b0;
        ex_mem_funct3 = idex_instr[14:12];
        ex_result = 32'd0;
        ex_store_data = 32'd0;
        ex_store_strobe = 4'b0000;
        ex_redirect = 1'b0;
        ex_redirect_pc = 32'd0;
        ex_exception = 1'b0;
        ex_exception_cause = 32'd0;
        ex_exception_tval = 32'd0;
        ex_csr_write = 1'b0;
        ex_csr_addr = idex_instr[31:20];
        ex_csr_wdata = 32'd0;
        ex_mret = 1'b0;
        ex_address = 32'd0;
        ex_target = 32'd0;
        ex_branch_taken = 1'b0;
        ex_legal = 1'b1;
        csr_operand = idex_instr[14] ? {27'd0, idex_instr[19:15]} : ex_rs1_value;
        csr_is_read_only = idex_instr[31:30] == 2'b11;

        if (idex_valid) begin
            case (idex_instr[6:0])
                7'b0110111: begin
                    ex_reg_write = 1'b1;
                    ex_result = ex_imm_u;
                end
                7'b0010111: begin
                    ex_reg_write = 1'b1;
                    ex_result = idex_pc + ex_imm_u;
                end
                7'b1101111: begin
                    ex_reg_write = 1'b1;
                    ex_result = idex_pc + 32'd4;
                    ex_target = idex_pc + ex_imm_j;
                    ex_redirect_pc = ex_target;
                    if (ex_target[1:0] != 2'b00) begin
                        ex_exception = 1'b1;
                        ex_exception_cause = CAUSE_INST_MISALIGNED;
                        ex_exception_tval = ex_target;
                    end else begin
                        ex_redirect = 1'b1;
                    end
                end
                7'b1100111: begin
                    if (idex_instr[14:12] == 3'b000) begin
                        ex_reg_write = 1'b1;
                        ex_result = idex_pc + 32'd4;
                        ex_target = (control_rs1_value + ex_imm_i) & 32'hfffffffe;
                        ex_redirect_pc = ex_target;
                        if (ex_target[1:0] != 2'b00) begin
                            ex_exception = 1'b1;
                            ex_exception_cause = CAUSE_INST_MISALIGNED;
                            ex_exception_tval = ex_target;
                        end else begin
                            ex_redirect = 1'b1;
                        end
                    end else begin
                        ex_legal = 1'b0;
                    end
                end
                7'b1100011: begin
                    // Compare the predicted target while evaluating the branch.
                    ex_target = idex_pc + ex_imm_b;
                    ex_redirect_pc = ex_target;
                    case (idex_instr[14:12])
                        3'b000: ex_branch_taken = branch_equal;
                        3'b001: ex_branch_taken = !branch_equal;
                        3'b100: ex_branch_taken = branch_less_signed;
                        3'b101: ex_branch_taken = !branch_less_signed;
                        3'b110: ex_branch_taken = branch_less_unsigned;
                        3'b111: ex_branch_taken = !branch_less_unsigned;
                        default: ex_legal = 1'b0;
                    endcase
                    if (ex_branch_taken && ex_legal) begin
                        if (ex_target[1:0] != 2'b00) begin
                            ex_exception = 1'b1;
                            ex_exception_cause = CAUSE_INST_MISALIGNED;
                            ex_exception_tval = ex_target;
                        end else begin
                            ex_redirect = 1'b1;
                        end
                    end
                end
                7'b0000011: begin
                    ex_address = ex_load_address;
                    ex_result = ex_address;
                    ex_mem_funct3 = idex_instr[14:12];
                    case (idex_instr[14:12])
                        3'b000,
                        3'b100: begin end
                        3'b001,
                        3'b101: if (ex_address[0]) begin
                            ex_exception = 1'b1;
                            ex_exception_cause = CAUSE_LOAD_MISALIGNED;
                            ex_exception_tval = ex_address;
                        end
                        3'b010: if (ex_address[1:0] != 2'b00) begin
                            ex_exception = 1'b1;
                            ex_exception_cause = CAUSE_LOAD_MISALIGNED;
                            ex_exception_tval = ex_address;
                        end
                        default: ex_legal = 1'b0;
                    endcase
                    if (!ex_exception && ex_legal) begin
                        ex_mem_read = 1'b1;
                        ex_reg_write = 1'b1;
                    end
                end
                7'b0100011: begin
                    ex_address = ex_alu_sum[31:0];
                    ex_result = ex_address;
                    ex_mem_funct3 = idex_instr[14:12];
                    case (idex_instr[14:12])
                        3'b000: begin
                            ex_store_data = ex_rs2_value << (ex_address[1:0] * 8);
                            ex_store_strobe = 4'b0001 << ex_address[1:0];
                        end
                        3'b001: begin
                            if (ex_address[0]) begin
                                ex_exception = 1'b1;
                                ex_exception_cause = CAUSE_STORE_MISALIGNED;
                                ex_exception_tval = ex_address;
                            end
                            ex_store_data = ex_rs2_value << (ex_address[1] * 16);
                            ex_store_strobe = 4'b0011 << (ex_address[1] * 2);
                        end
                        3'b010: begin
                            if (ex_address[1:0] != 2'b00) begin
                                ex_exception = 1'b1;
                                ex_exception_cause = CAUSE_STORE_MISALIGNED;
                                ex_exception_tval = ex_address;
                            end
                            ex_store_data = ex_rs2_value;
                            ex_store_strobe = 4'b1111;
                        end
                        default: ex_legal = 1'b0;
                    endcase
                    if (!ex_exception && ex_legal)
                        ex_mem_write = 1'b1;
                end
                7'b0010011: begin
                    ex_reg_write = 1'b1;
                    case (idex_instr[14:12])
                        3'b000: ex_result = ex_alu_sum[31:0];
                        3'b010: ex_result = {31'd0, ex_alu_less_signed};
                        3'b011: ex_result = {31'd0, ex_alu_less_unsigned};
                        3'b100: ex_result = ex_rs1_value ^ ex_alu_b;
                        3'b110: ex_result = ex_rs1_value | ex_alu_b;
                        3'b111: ex_result = ex_rs1_value & ex_alu_b;
                        3'b001: begin
                            if (idex_instr[31:25] == 7'b0000000)
                                ex_result = ex_shift_result;
                            else
                                ex_legal = 1'b0;
                        end
                        3'b101: begin
                            if (idex_instr[31:25] == 7'b0000000)
                                ex_result = ex_shift_result;
                            else if (idex_instr[31:25] == 7'b0100000)
                                ex_result = ex_shift_result;
                            else
                                ex_legal = 1'b0;
                        end
                        default: ex_legal = 1'b0;
                    endcase
                end
                7'b0110011: begin
                    ex_reg_write = 1'b1;
                    case (idex_instr[14:12])
                        3'b000: begin
                            if (idex_instr[31:25] == 7'b0000000)
                                ex_result = ex_alu_sum[31:0];
                            else if (idex_instr[31:25] == 7'b0100000)
                                ex_result = ex_alu_sum[31:0];
                            else if (idex_instr[31:25] == 7'b0000001)
                                ex_result = MULTIPLY_PIPELINE != 0 ? 32'd0 : mul_uu[31:0];
                            else
                                ex_legal = 1'b0;
                        end
                        3'b001: begin
                            if (idex_instr[31:25] == 7'b0000000) ex_result = ex_shift_result;
                            else if (idex_instr[31:25] == 7'b0000001)
                                ex_result = MULTIPLY_PIPELINE != 0 ? 32'd0 : mul_uu[63:32];
                            else ex_legal = 1'b0;
                        end
                        3'b010: begin
                            if (idex_instr[31:25] == 7'b0000000) ex_result = {31'd0, ex_alu_less_signed};
                            else if (idex_instr[31:25] == 7'b0000001)
                                ex_result = MULTIPLY_PIPELINE != 0 ? 32'd0 : mul_uu[63:32];
                            else ex_legal = 1'b0;
                        end
                        3'b011: begin
                            if (idex_instr[31:25] == 7'b0000000) ex_result = {31'd0, ex_alu_less_unsigned};
                            else if (idex_instr[31:25] == 7'b0000001)
                                ex_result = MULTIPLY_PIPELINE != 0 ? 32'd0 : mul_uu[63:32];
                            else ex_legal = 1'b0;
                        end
                        3'b100: if (idex_instr[31:25] == 7'b0000000) ex_result = ex_rs1_value ^ ex_alu_b; else ex_legal = 1'b0;
                        3'b101: begin
                            if (idex_instr[31:25] == 7'b0000000) ex_result = ex_shift_result;
                            else if (idex_instr[31:25] == 7'b0100000) ex_result = ex_shift_result;
                            else ex_legal = 1'b0;
                        end
                        3'b110: if (idex_instr[31:25] == 7'b0000000) ex_result = ex_rs1_value | ex_alu_b; else ex_legal = 1'b0;
                        3'b111: if (idex_instr[31:25] == 7'b0000000) ex_result = ex_rs1_value & ex_alu_b; else ex_legal = 1'b0;
                        default: ex_legal = 1'b0;
                    endcase
                end
                7'b0001111: begin
                    if (idex_instr[14:12] != 3'b000 && idex_instr[14:12] != 3'b001)
                        ex_legal = 1'b0;
                end
                7'b1110011: begin
                    if (idex_instr == 32'h00000073) begin
                        ex_exception = 1'b1;
                        ex_exception_cause = CAUSE_ECALL_M;
                    end else if (idex_instr == 32'h00100073) begin
                        ex_exception = 1'b1;
                        ex_exception_cause = CAUSE_BREAKPOINT;
                    end else if (idex_instr == 32'h30200073) begin
                        ex_mret = 1'b1;
                        ex_redirect = 1'b1;
                        ex_redirect_pc = csr_mepc;
                    end else if (idex_instr == 32'h10500073) begin
                        ex_result = 32'd0;
                    end else if (idex_instr[14:12] != 3'b000 && csr_address_valid) begin
                        ex_reg_write = 1'b1;
                        ex_result = csr_read_data;
                        case (idex_instr[14:12])
                            3'b001,
                            3'b101: begin
                                ex_csr_write = 1'b1;
                                ex_csr_wdata = csr_operand;
                            end
                            3'b010,
                            3'b110: begin
                                ex_csr_write = csr_write_requested;
                                ex_csr_wdata = csr_read_data | csr_operand;
                            end
                            3'b011,
                            3'b111: begin
                                ex_csr_write = csr_write_requested;
                                ex_csr_wdata = csr_read_data & ~csr_operand;
                            end
                            default: ex_legal = 1'b0;
                        endcase
                        if (csr_is_read_only && ex_csr_write)
                            ex_legal = 1'b0;
                    end else begin
                        ex_legal = 1'b0;
                    end
                end
                default: ex_legal = 1'b0;
            endcase

            if (!ex_legal && !ex_exception) begin
                ex_exception = 1'b1;
                ex_exception_cause = CAUSE_ILLEGAL;
                ex_exception_tval = idex_instr;
                ex_reg_write = 1'b0;
                ex_mem_read = 1'b0;
                ex_mem_write = 1'b0;
                ex_csr_write = 1'b0;
                ex_redirect = 1'b0;
            end
            if (WAIT_MEMORY != 0 && idex_access_error) begin
                ex_exception = 1'b1;
                ex_exception_cause = CAUSE_INST_ACCESS;
                ex_exception_tval = idex_pc;
                ex_reg_write = 1'b0;
                ex_mem_read = 1'b0;
                ex_mem_write = 1'b0;
                ex_csr_write = 1'b0;
                ex_redirect = 1'b0;
                ex_mret = 1'b0;
            end
        end
    end

    always @* begin
        case (exmem_funct3)
            3'b000: mem_load_data = {{24{dmem_rdata[(exmem_result[1:0] * 8) + 7]}}, dmem_rdata[(exmem_result[1:0] * 8) +: 8]};
            3'b001: mem_load_data = {{16{dmem_rdata[(exmem_result[1] * 16) + 15]}}, dmem_rdata[(exmem_result[1] * 16) +: 16]};
            3'b010: mem_load_data = dmem_rdata;
            3'b100: mem_load_data = {24'd0, dmem_rdata[(exmem_result[1:0] * 8) +: 8]};
            3'b101: mem_load_data = {16'd0, dmem_rdata[(exmem_result[1] * 16) +: 16]};
            default: mem_load_data = 32'd0;
        endcase
    end

    wire take_exception = idex_valid && ex_exception && execute_advance;
    wire take_trap = memory_fault || take_interrupt || take_exception;
    wire ex_is_branch = idex_instr[6:0] == 7'b1100011;
    wire ex_is_jump = idex_instr[6:0] == 7'b1101111 ||
                      idex_instr[6:0] == 7'b1100111;
    wire ex_control_transfer = ex_is_branch || ex_is_jump || ex_mret;
    // This PC is consumed only for a control recovery. Precise traps retain
    // priority in the sequential block, including illegal/misaligned controls.
    // Avoid sending the branch comparator through general legal/exception muxes.
    wire [31:0] ex_control_next_pc = ex_is_branch ?
        (branch_condition_raw ? idex_pc + ex_imm_b : idex_pc + 32'd4) :
        idex_instr[6:0] == 7'b1101111 ? idex_pc + ex_imm_j :
        idex_instr[6:0] == 7'b1100111 ? (control_rs1_value + ex_imm_i) & 32'hfffffffe : csr_mepc;
    wire [31:0] ex_actual_next_pc = FAST_REDIRECT_SELECT != 0 ? ex_control_next_pc :
        ex_redirect ? ex_redirect_pc : idex_pc + 32'd4;
    // JALR clears bit zero: the unmasked sum may equal predicted_pc or predicted_pc+1.
    wire ex_jalr_prediction_error = idex_pred_pc[0] ||
        (control_rs1_value != idex_jalr_expected0 && control_rs1_value != idex_jalr_expected1);
    // Decode target checks independently; the JALR adder must not feed the MRET comparison.
    wire ex_prediction_error = EARLY_BRANCH_CHECK != 0 && ex_is_branch ?
        ((FAST_REDIRECT_SELECT != 0 ? branch_condition_raw : ex_branch_taken) ?
            idex_branch_target_miss : idex_sequential_miss) :
        EARLY_BRANCH_CHECK != 0 && idex_instr[6:0] == 7'b1100111 ? ex_jalr_prediction_error :
        EARLY_BRANCH_CHECK != 0 && idex_instr[6:0] == 7'b1101111 ?
            idex_pred_pc != idex_pc + ex_imm_j :
        EARLY_BRANCH_CHECK != 0 && idex_instr == 32'h30200073 ? idex_pred_pc != csr_mepc :
        EARLY_BRANCH_CHECK != 0 ? idex_sequential_miss :
        (ex_redirect ? (idex_pred_pc != ex_redirect_pc) : (idex_pred_pc != idex_pc + 32'd4));
    wire ex_mispredict = idex_valid && ex_control_transfer && !ex_exception && ex_prediction_error;
    // The trap mux already has priority over redirects; its exception test need
    // not also delay the speculative recovery request on the fast control path.
    wire ex_recovery_requested = FAST_TRAP_PATH != 0 ?
        idex_valid && ex_control_transfer && ex_prediction_error : ex_mispredict;
    wire ex_rd_link = idex_rd == 5'd1 || idex_rd == 5'd5;
    wire ex_rs1_link = idex_rs1 == 5'd1 || idex_rs1 == 5'd5;
    wire ras_push = ex_is_jump && ex_rd_link;
    wire ras_pop = idex_instr[6:0] == 7'b1100111 && ex_rs1_link &&
                   (!ex_rd_link || idex_rd != idex_rs1);
    wire ex_fence_i = idex_valid && idex_instr[6:0] == 7'b0001111 && idex_instr[14:12] == 3'b001;
    wire [31:0] trap_cause = memory_fault ?
                             (exmem_mem_write ? CAUSE_STORE_ACCESS : CAUSE_LOAD_ACCESS) :
                             take_interrupt ? interrupt_cause : ex_exception_cause;
    // mtval has the same older-MEM > synchronous-EX > interrupt priority as
    // the trap controller. Select its data without the late interrupt mux:
    // when a trap is accepted and EX has no exception, the value is zero.
    wire [31:0] trap_tval = FAST_TRAP_PATH != 0 ?
        (memory_fault ? exmem_result : ex_exception ? ex_exception_tval : 32'd0) :
        (memory_fault ? exmem_result : take_interrupt ? 32'd0 : ex_exception_tval);
    // (exception || (irq && !exception)) == (exception || irq). Keep this
    // local enable apart from take_trap's frontend/CSR control fanout.
    (* keep = "true" *) wire mtval_trap_commit = memory_fault ||
        (idex_valid && execute_advance && (ex_exception || interrupt_enabled));
    // Keep the high increment independent of the late interrupt/exception mux.
    // Both supported vector offsets fit in the low eight word-address bits.
    (* keep = "true" *) wire [21:0] vector_high_next;
    (* keep = "true" *) wire [8:0] vector_low_timer, vector_low_external;
    assign vector_high_next = csr_mtvec[31:10] + 22'd1;
    assign vector_low_timer = {1'b0, csr_mtvec[9:2]} + 9'd7;
    assign vector_low_external = {1'b0, csr_mtvec[9:2]} + 9'd11;
    wire [8:0] vector_low = irq_external && csr_mie[11] ? vector_low_external : vector_low_timer;
    wire [31:0] interrupt_vector = {
        vector_low[8] ? vector_high_next : csr_mtvec[31:10], vector_low[7:0], 2'b00};
    wire [31:0] trap_vector = (take_interrupt && csr_mtvec[1:0] == 2'b01) ?
                              (FAST_TRAP_PATH != 0 ? interrupt_vector :
                               ({csr_mtvec[31:2], 2'b00} + {trap_cause[29:0], 2'b00})) :
                              {csr_mtvec[31:2], 2'b00};

    // CSR writes cannot be branch instructions; keep their enable independent of branch exceptions.
    wire csr_operation_valid = idex_instr[14:12] == 3'b001 || idex_instr[14:12] == 3'b010 ||
                               idex_instr[14:12] == 3'b011 || idex_instr[14:12] == 3'b101 ||
                               idex_instr[14:12] == 3'b110 || idex_instr[14:12] == 3'b111;
    wire fast_csr_commit = idex_valid && idex_instr[6:0] == 7'b1110011 &&
        csr_operation_valid && csr_address_valid && csr_write_requested &&
        idex_instr[31:30] != 2'b11 && !(WAIT_MEMORY != 0 && idex_access_error) &&
        execute_advance && !interrupt_enabled;
    wire csr_commit = FAST_TRAP_PATH != 0 ? fast_csr_commit :
        idex_valid && ex_csr_write && !take_trap && !memory_stall;
    wire [31:0] committed_csr_data = FAST_TRAP_PATH == 0 ? ex_csr_wdata :
        idex_instr[13:12] == 2'b01 ? csr_operand :
        idex_instr[13:12] == 2'b10 ? csr_read_data | csr_operand : csr_read_data & ~csr_operand;
    // Predecoded addresses and funct3 are known legal writable counter CSRs.
    // Faults in older MEM and pending IRQs retain priority, including stalls.
    wire counter_write_allowed = idex_valid &&
        !(WAIT_MEMORY != 0 && idex_access_error) && execute_advance && !interrupt_enabled;
    wire [3:0] counter_write_commit = COUNTER_CSR_PREDECODE != 0 ?
        idex_counter_csr_writes & {4{counter_write_allowed}} : {
            csr_commit && ex_csr_addr == 12'hb82,
            csr_commit && ex_csr_addr == 12'hb02,
            csr_commit && ex_csr_addr == 12'hb80,
            csr_commit && ex_csr_addr == 12'hb00
        };

    localparam integer FRONTEND_DECOUPLED = DECOUPLE_FETCH != 0 || FETCH_PIPE_STAGES != 0;
    wire fetch_response_usable = imem_ready && fetch_pending && !fetch_killed;
    wire frontend_advance = !memory_stall && !load_use_stall;
    wire fetch_input_valid = WAIT_MEMORY == 0 ? rst_n : fetch_response_usable;
    wire [96:0] fetch_input_packet = {
        WAIT_MEMORY != 0 && imem_error,
        WAIT_MEMORY == 0 ? fetch_pc : fetch_request_pc,
        WAIT_MEMORY == 0 ? fetch_next_pc : fetch_request_pred_pc,
        imem_rdata
    };
    wire frontend_input_ready, frontend_valid;
    wire [96:0] frontend_packet;
    wire frontend_access_error = frontend_packet[96];
    wire [31:0] frontend_pc = frontend_packet[95:64];
    wire [31:0] frontend_pred_pc = frontend_packet[63:32];
    wire [31:0] frontend_instr = frontend_packet[31:0];

    // Each FD stage is a registered elastic instruction packet, including its fault.
    generate if (FETCH_PIPE_STAGES == 0) begin : direct_fetch
        assign frontend_input_ready = frontend_advance;
        assign frontend_valid = fetch_input_valid;
        assign frontend_packet = fetch_input_packet;
    end else begin : registered_fetch
        reg [FETCH_PIPE_STAGES-1:0] valid;
        reg [96:0] packet [0:FETCH_PIPE_STAGES-1];
        wire [FETCH_PIPE_STAGES:0] ready;
        assign ready[FETCH_PIPE_STAGES] = frontend_advance;
        assign frontend_input_ready = ready[0];
        assign frontend_valid = valid[FETCH_PIPE_STAGES-1];
        assign frontend_packet = packet[FETCH_PIPE_STAGES-1];
        genvar stage;
        for (stage = 0; stage < FETCH_PIPE_STAGES; stage = stage + 1) begin : fd
            wire incoming_valid;
            wire [96:0] incoming_packet;
            assign ready[stage] = !valid[stage] || ready[stage+1];
            if (stage == 0) begin : first
                assign incoming_valid = fetch_input_valid;
                assign incoming_packet = fetch_input_packet;
            end else begin : following
                assign incoming_valid = valid[stage-1];
                assign incoming_packet = packet[stage-1];
            end
            always @(posedge clk) begin
                if (rst_n && ready[stage] && incoming_valid)
                    packet[stage] <= incoming_packet;
            end
            always @(posedge clk or negedge rst_n) begin
                if (!rst_n) valid[stage] <= 0;
                else if (fetch_flush) valid[stage] <= 0;
                else if (ready[stage]) valid[stage] <= incoming_valid;
            end
        end
    end endgenerate

    wire fetch_request_fire = imem_valid && imem_req_ready;
    assign imem_valid = rst_n && (WAIT_MEMORY == 0 ||
                         ((FRONTEND_DECOUPLED != 0 || !fetch_flush) &&
                          (!fetch_pending || imem_consume)));
    assign imem_addr = fetch_pc;
    assign fence_i = ex_fence_i && execute_advance && !interrupt_enabled &&
                     !(WAIT_MEMORY != 0 && idex_access_error);
    assign fetch_flush = take_trap || (execute_advance && (ex_recovery_requested || fence_i));
    assign imem_consume = rst_n && (WAIT_MEMORY == 0 ?
                           ((FRONTEND_DECOUPLED != 0 || !fetch_flush) && frontend_input_ready) :
                           (imem_ready && fetch_pending &&
                            (fetch_killed || frontend_input_ready ||
                             (FRONTEND_DECOUPLED == 0 && fetch_flush))));
    assign dmem_valid = exmem_valid && (exmem_mem_read || exmem_mem_write);
    assign dmem_we = exmem_mem_write;
    assign dmem_addr = exmem_result;
    assign dmem_wdata = exmem_store_data;
    assign dmem_wstrb = exmem_store_strobe;
    wire early_load_width_valid = idex_instr[14:12] == 3'b000 || idex_instr[14:12] == 3'b100 ||
        ((idex_instr[14:12] == 3'b001 || idex_instr[14:12] == 3'b101) && !ex_load_address[0]) ||
        (idex_instr[14:12] == 3'b010 && ex_load_address[1:0] == 0);
    // A signed 12-bit offset cannot reach MMIO from these base prefixes.
    wire early_load_region = ex_rs1_value[31:24] == 8'h00 ||
        ex_rs1_value[31:24] == 8'h10 || ex_rs1_value[31:24] == 8'h40;
    // Only an advancing EX load can start early; MMIO uses the MEM-stage path.
    assign dmem_early_valid = EARLY_LOAD_REQUEST != 0 && WAIT_MEMORY != 0 && idex_valid &&
        idex_instr[6:0] == 7'b0000011 && early_load_width_valid && !idex_access_error &&
        !interrupt_enabled && !dmem_valid && early_load_region;
    assign dmem_early_addr = ex_load_address;
    assign current_pc = fetch_pc;
    assign debug_x10 = regs_valid[10] ? regs[10] : 32'd0;
    assign cycle_count = csr_mcycle;
    assign instret_count = csr_minstret;

    // Reset validity only; unreset data arrays can infer distributed RAM.
    always @(posedge clk) begin
        if (rst_n) begin
            if (MULTIPLY_PIPELINE != 0 && idex_mul && execute_advance) begin
                // Four independent 17-bit signed products fit the native APMs.
                // The MEM-stage carry merge also supplies all high-half forms.
                mul_ll <= mul_low_a * mul_low_b;
                mul_lh <= mul_low_a * mul_high_b;
                mul_hl <= mul_high_a * mul_low_b;
                mul_hh <= mul_high_a * mul_high_b;
            end
            if (memwb_valid && memwb_reg_write && memwb_rd != 5'd0)
                regs[memwb_rd] <= memwb_data;
            if (RAS_BITS != 0 && idex_valid && execute_advance && !take_trap && ex_legal && ex_redirect && ras_push)
                ras_data[(ras_pop && ras_count != 0) ? ras_top_index : ras_pointer] <= idex_pc + 32'd4;
            if (idex_valid && execute_advance && !take_trap && ex_legal) begin
                if (ex_is_branch) begin
                    if (ex_branch_taken) begin
                        btb_return[ex_btb_index] <= 1'b0;
                        btb_tag[ex_btb_index] <= idex_pc[31:BTB_INDEX_BITS+2];
                        btb_target[ex_btb_index] <= ex_target;
                        btb_counter[ex_btb_index] <= ex_btb_hit ?
                            (btb_counter[ex_btb_index] == 2'b11 ? 2'b11 : btb_counter[ex_btb_index] + 2'd1) :
                            2'b10;
                    end else if (ex_btb_hit) begin
                        btb_counter[ex_btb_index] <= btb_counter[ex_btb_index] == 2'b00 ?
                            2'b00 : btb_counter[ex_btb_index] - 2'd1;
                    end
                end else if (ex_is_jump && ex_redirect) begin
                    btb_return[ex_btb_index] <= idex_instr[6:0] == 7'b1100111 &&
                                               idex_instr[31:20] == 0 && ex_rs1_link && idex_rd != idex_rs1;
                    btb_tag[ex_btb_index] <= idex_pc[31:BTB_INDEX_BITS+2];
                    btb_target[ex_btb_index] <= ex_target;
                    btb_counter[ex_btb_index] <= 2'b11;
                end
            end
        end
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            fetch_pc <= 32'd0;
            fetch_pending <= 1'b0;
            fetch_killed <= 1'b0;
            fetch_request_pc <= 0;
            fetch_request_pred_pc <= 0;
            ifid_valid <= 1'b0;
            idex_valid <= 1'b0;
            exmem_valid <= 1'b0;
            memwb_valid <= 1'b0;
            ifid_pc <= 32'd0;
            ifid_instr <= 32'h00000013;
            ifid_pred_pc <= 32'd0;
            ifid_access_error <= 1'b0;
            idex_pc <= 32'd0;
            idex_instr <= 32'h00000013;
            idex_pred_pc <= 32'd0;
            idex_branch_target_miss <= 1'b0;
            idex_sequential_miss <= 1'b0;
            idex_jalr_expected0 <= 0;
            idex_jalr_expected1 <= 0;
            idex_access_error <= 1'b0;
            idex_rs1 <= 5'd0;
            idex_rs2 <= 5'd0;
            idex_rd <= 5'd0;
            idex_rs1_value <= 32'd0;
            idex_rs2_value <= 32'd0;
            idex_exmem_rs1_match <= 1'b0;
            idex_exmem_rs2_match <= 1'b0;
            idex_memwb_rs1_match <= 1'b0;
            idex_memwb_rs2_match <= 1'b0;
            idex_counter_csr_writes <= 4'd0;
            idex_csr_address_valid <= 1'b0;
            exmem_rd <= 5'd0;
            exmem_pc <= 32'd0;
            exmem_reg_write <= 1'b0;
            exmem_mem_read <= 1'b0;
            exmem_mem_write <= 1'b0;
            exmem_funct3 <= 3'd0;
            exmem_result <= 32'd0;
            exmem_mul_high <= 1'b0;
            exmem_mul_rs1 <= 32'd0;
            exmem_mul_rs2 <= 32'd0;
            exmem_store_data <= 32'd0;
            exmem_store_strobe <= 4'd0;
            memwb_rd <= 5'd0;
            memwb_reg_write <= 1'b0;
            memwb_data <= 32'd0;
            csr_mstatus <= MSTATUS_MPP_MACHINE;
            csr_mie <= 32'd0;
            csr_mtvec <= 32'd0;
            csr_mscratch <= 32'd0;
            csr_mepc <= 32'd0;
            csr_mcause <= 32'd0;
            csr_mtval <= 32'd0;
            csr_mcycle <= 64'd0;
            csr_minstret <= 64'd0;
            trap_taken <= 1'b0;
            regs_valid <= 32'd0;
            ras_pointer <= 0;
            ras_count <= 0;
            btb_valid <= {BTB_ENTRIES{1'b0}};
        end else begin
            trap_taken <= 1'b0;
            if (WAIT_MEMORY != 0) begin
                if (imem_consume) fetch_pending <= 0;
                if (fetch_request_fire) begin
                    fetch_pending <= 1;
                    // A request accepted on a redirect still used the old fetch PC.
                    fetch_killed <= fetch_flush;
                    fetch_request_pc <= fetch_pc;
                    fetch_request_pred_pc <= fetch_next_pc;
                    fetch_pc <= fetch_next_pc;
                end
                if (fetch_flush && fetch_pending && !imem_consume) fetch_killed <= 1;
            end else if (fetch_input_valid && imem_consume) fetch_pc <= fetch_next_pc;
            csr_mcycle <= FAST_TRAP_PATH != 0 ? increment_counter(csr_mcycle) : csr_mcycle + 64'd1;
            if (memwb_valid)
                csr_minstret <= FAST_TRAP_PATH != 0 ? increment_counter(csr_minstret) : csr_minstret + 64'd1;

            if (csr_commit) begin
                case (ex_csr_addr)
                    12'h300: csr_mstatus <= (committed_csr_data & MSTATUS_WRITABLE_MASK) |
                                           MSTATUS_MPP_MACHINE;
                    12'h304: csr_mie <= committed_csr_data & MIE_WRITABLE_MASK;
                    // Reserved modes are mapped to the supported direct mode.
                    12'h305: csr_mtvec <= {committed_csr_data[31:2], 1'b0,
                                          committed_csr_data[1:0] == 2'b01};
                    12'h340: csr_mscratch <= committed_csr_data;
                    12'h341: csr_mepc <= {committed_csr_data[31:2], 2'b00};
                    12'h342: csr_mcause <= committed_csr_data;
                    12'h343: csr_mtval <= committed_csr_data;
                    // misa is fixed WARL; mip's supported pending bits come
                    // from the hardware inputs.  Legal writes leave both alone.
                    default: begin end
                endcase
            end
            // Half writes keep their original precedence over that edge's
            // increment, without moving CSR effects to a later pipeline stage.
            if (counter_write_commit[0]) csr_mcycle[31:0] <= committed_csr_data;
            if (counter_write_commit[1]) csr_mcycle[63:32] <= committed_csr_data;
            if (counter_write_commit[2]) csr_minstret[31:0] <= committed_csr_data;
            if (counter_write_commit[3]) csr_minstret[63:32] <= committed_csr_data;

            if (memwb_valid && memwb_reg_write && memwb_rd != 5'd0)
                regs_valid[memwb_rd] <= 1'b1;

            if (RAS_BITS != 0 && idex_valid && execute_advance && !take_trap && ex_legal && ex_redirect) begin
                if (ras_pop && !ras_push && ras_count != 0) begin
                    ras_pointer <= ras_top_index;
                    ras_count <= ras_count - 1'b1;
                end else if (ras_push && !(ras_pop && ras_count != 0)) begin
                    ras_pointer <= ras_pointer + 1'b1;
                    if (ras_count < RAS_ENTRIES)
                        ras_count <= ras_count + 1'b1;
                end
            end

            memwb_valid <= exmem_valid && !memory_stall && !memory_fault;
            memwb_rd <= exmem_rd;
            memwb_reg_write <= exmem_reg_write;
            memwb_data <= exmem_mem_read ? mem_load_data :
                          exmem_mul_high ? mem_mul_high_result : exmem_result;

            // Validity suppresses trapped instructions; payload data only needs MEM backpressure.
            if (SEPARATE_EXMEM_PAYLOAD != 0 && !memory_stall) begin
                exmem_pc <= idex_pc;
                exmem_result <= ex_result;
                exmem_mul_rs1 <= mul_rs1_value;
                exmem_mul_rs2 <= mul_rs2_value;
                exmem_store_data <= ex_store_data;
            end

            // Flushes clear validity; speculative payloads can use local stall enables.
            if (SEPARATE_FRONTEND_PAYLOAD != 0 && !memory_stall && !load_use_stall) begin
                idex_pc <= ifid_pc;
                idex_instr <= ifid_instr;
                idex_counter_csr_writes <= ifid_counter_csr_writes;
                idex_csr_address_valid <= ifid_csr_address_valid;
                idex_pred_pc <= ifid_pred_pc;
                idex_branch_target_miss <= ifid_branch_target_miss;
                idex_sequential_miss <= ifid_sequential_miss;
                idex_jalr_expected0 <= ifid_jalr_expected0;
                idex_jalr_expected1 <= ifid_jalr_expected1;
                idex_access_error <= ifid_access_error;
                idex_rs1 <= ifid_rs1;
                idex_rs2 <= ifid_rs2;
                idex_rd <= ifid_rd;
                idex_rs1_value <= id_rs1_value;
                idex_rs2_value <= id_rs2_value;
                // These producers advance to MEM/WB with the decoded consumer.
                idex_exmem_rs1_match <= idex_rd == ifid_rs1;
                idex_exmem_rs2_match <= idex_rd == ifid_rs2;
                idex_memwb_rs1_match <= exmem_rd == ifid_rs1;
                idex_memwb_rs2_match <= exmem_rd == ifid_rs2;
                if (frontend_valid) begin
                    ifid_pc <= frontend_pc;
                    ifid_instr <= frontend_instr;
                    ifid_pred_pc <= frontend_pred_pc;
                    ifid_access_error <= frontend_access_error;
                end
            end

            if (idex_valid && execute_advance && !take_trap && ex_legal &&
                ((ex_is_branch && ex_branch_taken) || (ex_is_jump && ex_redirect)))
                btb_valid[ex_btb_index] <= 1'b1;

            if (FAST_TRAP_PATH != 0 && mtval_trap_commit)
                csr_mtval <= trap_tval;
            if (take_trap) begin
                exmem_valid <= 1'b0;
                exmem_mul_high <= 1'b0;
                idex_valid <= 1'b0;
                ifid_valid <= 1'b0;
                fetch_pc <= trap_vector;
                csr_mepc <= memory_fault ? exmem_pc : idex_pc;
                csr_mcause <= trap_cause;
                if (FAST_TRAP_PATH == 0) csr_mtval <= trap_tval;
                csr_mstatus[7] <= csr_mstatus[3];
                csr_mstatus[3] <= 1'b0;
                trap_taken <= 1'b1;
                ras_count <= 0;
                ras_pointer <= 0;
            end else if (memory_stall) begin
                // WB still retires once while MEM waits; preserve its bypasses
                // for the younger instruction held in EX during that wait.
                if (memwb_valid && memwb_reg_write && memwb_rd != 0) begin
                    if (memwb_rs1_match) idex_rs1_value <= memwb_data;
                    if (memwb_rs2_match) idex_rs2_value <= memwb_data;
                end
            end else begin
                exmem_valid <= idex_valid;
                exmem_rd <= idex_rd;
                exmem_reg_write <= ex_reg_write;
                exmem_mem_read <= ex_mem_read;
                exmem_mem_write <= ex_mem_write;
                exmem_funct3 <= ex_mem_funct3;
                exmem_mul_high <= idex_mul_high;
                if (SEPARATE_EXMEM_PAYLOAD == 0) begin
                    exmem_pc <= idex_pc;
                    exmem_result <= ex_result;
                    exmem_mul_rs1 <= mul_rs1_value;
                    exmem_mul_rs2 <= mul_rs2_value;
                    exmem_store_data <= ex_store_data;
                end
                exmem_store_strobe <= ex_store_strobe;

                if (ex_mret && idex_valid) begin
                    csr_mstatus[3] <= csr_mstatus[7];
                    csr_mstatus[7] <= 1'b1;
                    ras_count <= 0;
                    ras_pointer <= 0;
                end

                if (fence_i) begin
                    btb_valid <= 0;
                    ras_count <= 0;
                    ras_pointer <= 0;
                end
                if (ex_recovery_requested || fence_i) begin
                    idex_valid <= 1'b0;
                    ifid_valid <= 1'b0;
                    fetch_pc <= fence_i ? idex_pc + 32'd4 : ex_actual_next_pc;
                end else if (load_use_stall) begin
                    idex_valid <= 1'b0;
                end else begin
                    idex_valid <= ifid_valid;
                    if (SEPARATE_FRONTEND_PAYLOAD == 0) begin
                        idex_pc <= ifid_pc;
                        idex_instr <= ifid_instr;
                        idex_counter_csr_writes <= ifid_counter_csr_writes;
                        idex_csr_address_valid <= ifid_csr_address_valid;
                        idex_pred_pc <= ifid_pred_pc;
                        idex_branch_target_miss <= ifid_branch_target_miss;
                        idex_sequential_miss <= ifid_sequential_miss;
                        idex_jalr_expected0 <= ifid_jalr_expected0;
                        idex_jalr_expected1 <= ifid_jalr_expected1;
                        idex_access_error <= ifid_access_error;
                        idex_rs1 <= ifid_rs1;
                        idex_rs2 <= ifid_rs2;
                        idex_rd <= ifid_rd;
                        idex_rs1_value <= id_rs1_value;
                        idex_rs2_value <= id_rs2_value;
                        idex_exmem_rs1_match <= idex_rd == ifid_rs1;
                        idex_exmem_rs2_match <= idex_rd == ifid_rs2;
                        idex_memwb_rs1_match <= exmem_rd == ifid_rs1;
                        idex_memwb_rs2_match <= exmem_rd == ifid_rs2;
                    end
                    ifid_valid <= frontend_valid;
                    if (frontend_valid) begin
                        if (SEPARATE_FRONTEND_PAYLOAD == 0) begin
                            ifid_pc <= frontend_pc;
                            ifid_instr <= frontend_instr;
                            ifid_pred_pc <= frontend_pred_pc;
                            ifid_access_error <= frontend_access_error;
                        end
                    end
                end
            end
        end
    end

endmodule

