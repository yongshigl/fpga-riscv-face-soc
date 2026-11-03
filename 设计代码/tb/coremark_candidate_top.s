`timescale 1ns/1ps

module coremark_soc_top #(
    parameter integer IMEM_WORDS = 4096,
    parameter integer DMEM_WORDS = 1024,
    parameter integer DATA_ROM_ENABLE = 1,
    parameter integer CACHE_ENABLE = 1,
    parameter integer ICACHE_SET_BITS = 7,
    parameter integer DCACHE_SET_BITS = 6,
    parameter IMEM_INIT_FILE = "",
    parameter DMEM_INIT_FILE = ""
) (
    input  wire        clk,
    input  wire        rst_n,
    output wire [31:0] gpio_out,
    output wire [7:0]  uart_debug_data,
    output wire        uart_debug_strobe,
    output wire [31:0] current_pc,
    output wire        trap_taken,
    output wire [63:0] cycle_count,
    output wire [63:0] instret_count,
    output wire [31:0] debug_ra,
    output wire [31:0] debug_sp,
    output wire [31:0] debug_stack_ra,
    output wire        debug_dmem_valid,
    output wire [31:0] debug_dmem_addr,
    output wire        profile_idex_valid,
    output wire        profile_mispredict,
    output wire        profile_load_stall,
    output wire        profile_branch,
    output wire        profile_jal,
    output wire        profile_jalr,
    output wire [31:0] profile_ex_pc,
    output wire        profile_memory_stall,
    output wire        profile_store,
    output wire        profile_load,
    output wire        profile_fetch_wait,
    output wire        profile_frontend_bubble,
    output wire [31:0] icache_hits, icache_misses, dcache_hits, dcache_misses
);

    wire uart_tx_pin_unused;
    assign debug_ra = soc.core.regs[1];
    assign debug_sp = soc.core.regs[2];
    assign debug_dmem_valid = soc.dmem_valid;
    assign debug_dmem_addr = soc.dmem_addr;
`ifdef CPU_CACHED_SOC
    assign debug_stack_ra = soc.memory.data_mem[DMEM_WORDS-1];
    assign profile_idex_valid = soc.core.idex_valid && soc.core.execute_advance;
`else
    assign debug_stack_ra = soc.data_mem[DMEM_WORDS-1];
    assign profile_idex_valid = soc.core.idex_valid;
    assign icache_hits = 0;
    assign icache_misses = 0;
    assign dcache_hits = 0;
    assign dcache_misses = 0;
`endif
    assign profile_mispredict = soc.core.ex_mispredict;
    assign profile_load_stall = soc.core.load_use_stall;
    assign profile_branch = soc.core.ex_is_branch;
    assign profile_jal = soc.core.idex_instr[6:0] == 7'b1101111;
    assign profile_jalr = soc.core.idex_instr[6:0] == 7'b1100111;
    assign profile_ex_pc = soc.core.idex_pc;
    assign profile_memory_stall = soc.core.memory_stall;
    assign profile_store = soc.core.exmem_mem_write;
    assign profile_load = soc.core.exmem_mem_read;
    assign profile_fetch_wait = soc.core.fetch_pending && !soc.core.imem_ready;
    assign profile_frontend_bubble = !soc.core.ifid_valid && !soc.core.memory_stall;

`ifdef CPU_CACHED_SOC
    wire external_valid_unused, external_write_unused, external_ready_unused;
    wire [31:0] external_addr_unused, external_strobe_unused;
    wire [255:0] external_data_unused;
    wire accelerator_valid_unused, accelerator_write_unused, accelerator_ready_unused;
    wire [31:0] accelerator_addr_unused, accelerator_data_unused;
    wire [3:0] accelerator_strobe_unused;
    rv32i_cached_soc #(
        .CACHE_ENABLE(CACHE_ENABLE), .BTB_BITS(`CPU_BTB_INDEX_BITS), .RAS_BITS(`CPU_RAS_BITS),
        .ICACHE_SET_BITS(ICACHE_SET_BITS), .DCACHE_SET_BITS(DCACHE_SET_BITS),
        .CONTROL_LOAD_GUARD(`CPU_CONTROL_LOAD_GUARD), .MULTIPLY_PIPELINE(`CPU_MULTIPLY_PIPELINE),
        .LOAD_USE_GUARD(`CPU_LOAD_USE_GUARD),
`else
    rv32i_pipeline_soc #(
`endif
        .IMEM_WORDS(IMEM_WORDS),
        .DMEM_WORDS(DMEM_WORDS),
        .DATA_ROM_ENABLE(DATA_ROM_ENABLE),
        .IMEM_INIT_FILE(IMEM_INIT_FILE),
        .DMEM_INIT_FILE(DMEM_INIT_FILE),
        .CLOCK_HZ(400),
        .UART_BAUD(100)
    ) soc (
        .clk(clk), .rst_n(rst_n), .external_irq(1'b0), .gpio_out(gpio_out),
        .uart_tx_pin(uart_tx_pin_unused), .uart_debug_data(uart_debug_data),
        .uart_debug_strobe(uart_debug_strobe), .current_pc(current_pc),
        .trap_taken(trap_taken), .cycle_count(cycle_count), .instret_count(instret_count)
`ifdef CPU_CACHED_SOC
        , .uart_rx_pin(1'b1), .icache_hits(icache_hits), .icache_misses(icache_misses), .dcache_hits(dcache_hits), .dcache_misses(dcache_misses)
        , .external_req_valid(external_valid_unused), .external_req_ready(1'b1),
        .external_req_write(external_write_unused), .external_req_addr(external_addr_unused),
        .external_req_wdata(external_data_unused), .external_req_wstrb(external_strobe_unused),
        .external_rsp_valid(1'b1), .external_rsp_ready(external_ready_unused),
        .external_rsp_rdata(256'd0), .external_rsp_error(1'b1),
        .accelerator_req_valid(accelerator_valid_unused), .accelerator_req_ready(1'b1),
        .accelerator_req_write(accelerator_write_unused), .accelerator_req_addr(accelerator_addr_unused),
        .accelerator_req_wdata(accelerator_data_unused), .accelerator_req_wstrb(accelerator_strobe_unused),
        .accelerator_rsp_valid(1'b1), .accelerator_rsp_ready(accelerator_ready_unused),
        .accelerator_rsp_rdata(32'd0), .accelerator_rsp_error(1'b1)
`endif
    );

endmodule
