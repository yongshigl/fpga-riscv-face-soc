`timescale 1ns/1ps

module coremark_soc_top (
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
    output wire [31:0] profile_ex_pc
);

    wire uart_tx_pin_unused;
    assign debug_ra = soc.core.regs[1];
    assign debug_sp = soc.core.regs[2];
    assign debug_dmem_valid = soc.dmem_valid;
    assign debug_dmem_addr = soc.dmem_addr;
`ifdef COREMARK_COMPACT_3K
    assign debug_stack_ra = soc.data_mem[767];
`elsif COREMARK_COMPACT_4K
    assign debug_stack_ra = soc.data_mem[1023];
`elsif COREMARK_COMPACT_8K
    assign debug_stack_ra = soc.data_mem[2047];
`else
    assign debug_stack_ra = soc.data_mem[8191];
`endif
    assign profile_idex_valid = soc.core.idex_valid;
    assign profile_mispredict = soc.core.ex_mispredict;
    assign profile_load_stall = soc.core.load_use_stall;
    assign profile_branch = soc.core.ex_is_branch;
    assign profile_jal = soc.core.idex_instr[6:0] == 7'b1101111;
    assign profile_jalr = soc.core.idex_instr[6:0] == 7'b1100111;
    assign profile_ex_pc = soc.core.idex_pc;

`ifdef COREMARK_COMPACT_3K
    localparam TEXT_IMAGE = "build/compact_3k/coremark_text.hex";
    localparam DATA_IMAGE = "build/compact_3k/coremark_data.hex";
    localparam DATA_WORDS = 768;
`elsif COREMARK_COMPACT_4K
    localparam TEXT_IMAGE = "build/compact_4k/coremark_text.hex";
    localparam DATA_IMAGE = "build/compact_4k/coremark_data.hex";
    localparam DATA_WORDS = 1024;
`elsif COREMARK_COMPACT_8K
    localparam TEXT_IMAGE = "build/compact_8k/coremark_text.hex";
    localparam DATA_IMAGE = "build/compact_8k/coremark_data.hex";
    localparam DATA_WORDS = 2048;
`elsif COREMARK_MUL_O3
    localparam TEXT_IMAGE = "build/mul/o3/coremark_text.hex";
    localparam DATA_IMAGE = "build/mul/o3/coremark_data.hex";
    localparam DATA_WORDS = 8192;
`elsif COREMARK_MUL
    localparam TEXT_IMAGE = "build/mul/coremark_text.hex";
    localparam DATA_IMAGE = "build/mul/coremark_data.hex";
    localparam DATA_WORDS = 8192;
`else
    localparam TEXT_IMAGE = "build/coremark_text.hex";
    localparam DATA_IMAGE = "build/coremark_data.hex";
    localparam DATA_WORDS = 8192;
`endif

    rv32i_pipeline_soc #(
        .IMEM_WORDS(16384),
        .DMEM_WORDS(DATA_WORDS),
        .IMEM_INIT_FILE(TEXT_IMAGE),
        .DMEM_INIT_FILE(DATA_IMAGE),
        .CLOCK_HZ(400),
        .UART_BAUD(100)
    ) soc (
        .clk(clk), .rst_n(rst_n), .external_irq(1'b0), .gpio_out(gpio_out),
        .uart_tx_pin(uart_tx_pin_unused), .uart_debug_data(uart_debug_data),
        .uart_debug_strobe(uart_debug_strobe), .current_pc(current_pc),
        .trap_taken(trap_taken), .cycle_count(cycle_count), .instret_count(instret_count)
    );

endmodule
