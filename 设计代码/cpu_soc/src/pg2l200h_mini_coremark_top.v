`timescale 1ns/1ps
`include "src/cpu_config.vh"

module pg2l200h_mini_coremark_top (
    input  wire       clk,
    input  wire       rstn,
    output wire [7:0] led,
    output wire       uart_tx
);

    wire cpu_clk;
    wire pll_lock;
    reg [1:0] reset_sync;
    wire soc_rst_n = reset_sync[1];
    wire [31:0] gpio_out;
    wire [63:0] cycle_count;
    wire trap_taken;
    wire [7:0] uart_debug_data_unused;
    wire uart_debug_strobe_unused;
    wire [31:0] current_pc_unused;
    wire [63:0] instret_count_unused;

    // The firmware manifest and generated clock constraint use this PLL ratio.
    GTP_GPLL #(
        .CLKIN_FREQ(27.0),
        .STATIC_RATIOI(1), .STATIC_RATIOM(2),
        .STATIC_RATIOF(`CPU_PLL_FEEDBACK), .STATIC_RATIO0(`CPU_PLL_DIVIDER),
        .STATIC_RATIO1(2),
        .STATIC_RATIO2(15), .STATIC_RATIO3(15), .STATIC_RATIO4(15),
        .STATIC_RATIO5(15), .STATIC_RATIO6(15),
        .STATIC_DUTYF(`CPU_PLL_FEEDBACK), .STATIC_DUTY0(`CPU_PLL_DIVIDER), .STATIC_DUTY1(2),
        .STATIC_DUTY2(15), .STATIC_DUTY3(15), .STATIC_DUTY4(15),
        .STATIC_DUTY5(15), .STATIC_DUTY6(15)
    ) clock_pll (
        .CLKIN1(clk), .CLKIN2(1'b0), .CLKFB(1'b0), .CLKIN_SEL(1'b0),
        .CLKOUT0(cpu_clk), .LOCK(pll_lock),
        .DPS_CLK(1'b0), .DPS_EN(1'b0), .DPS_DIR(1'b0),
        .CLKOUT0_SYN(1'b0), .CLKOUT1_SYN(1'b0), .CLKOUT2_SYN(1'b0),
        .CLKOUT3_SYN(1'b0), .CLKOUT4_SYN(1'b0), .CLKOUT5_SYN(1'b0),
        .CLKOUT6_SYN(1'b0), .CLKOUTF_SYN(1'b0),
        .PLL_PWD(1'b0), .RST(1'b0),
        .APB_CLK(1'b0), .APB_RST_N(1'b0), .APB_ADDR(5'b0),
        .APB_SEL(1'b0), .APB_EN(1'b0), .APB_WRITE(1'b0),
        .APB_WDATA(16'b0)
    );

    always @(posedge cpu_clk or negedge rstn or negedge pll_lock) begin
        if (!rstn || !pll_lock)
            reset_sync <= 2'b00;
        else
            reset_sync <= {reset_sync[0], 1'b1};
    end

    assign led[7:1] = gpio_out[7:1];
    assign led[0] = gpio_out[0] | cycle_count[24] | trap_taken;

`ifdef CPU_CACHED_SOC
    wire external_valid_unused, external_write_unused, external_rsp_ready_unused;
    wire [31:0] external_addr_unused, external_strobe_unused;
    wire [255:0] external_data_unused;
    wire accelerator_valid_unused, accelerator_write_unused, accelerator_rsp_ready_unused;
    wire [31:0] accelerator_addr_unused, accelerator_data_unused;
    wire [3:0] accelerator_strobe_unused;
    wire [31:0] icache_hits_unused, icache_misses_unused, dcache_hits_unused, dcache_misses_unused;
    rv32i_cached_soc #(
        .ICACHE_SET_BITS(`CPU_ICACHE_SET_BITS), .DCACHE_SET_BITS(`CPU_DCACHE_SET_BITS),
        .BTB_BITS(`CPU_BTB_INDEX_BITS), .RAS_BITS(`CPU_RAS_BITS),
        .CONTROL_LOAD_GUARD(`CPU_CONTROL_LOAD_GUARD), .LOAD_USE_GUARD(`CPU_LOAD_USE_GUARD),
        .MULTIPLY_PIPELINE(`CPU_MULTIPLY_PIPELINE),
`else
    rv32i_pipeline_soc #(
`endif
        .IMEM_WORDS(4096),
        .DMEM_WORDS(`CPU_RAM_WORDS),
        .DATA_ROM_ENABLE(`CPU_DATA_ROM_ENABLE),
        .IMEM_INIT_FILE("mem/coremark_board_text.hex"),
        .DMEM_INIT_FILE("mem/coremark_board_data.hex"),
        .CLOCK_HZ(`CPU_CLOCK_HZ),
        .UART_BAUD(115_200)
    ) soc (
        .clk(cpu_clk), .rst_n(soc_rst_n), .external_irq(1'b0),
        .gpio_out(gpio_out), .uart_tx_pin(uart_tx),
        .uart_debug_data(uart_debug_data_unused),
        .uart_debug_strobe(uart_debug_strobe_unused),
        .current_pc(current_pc_unused), .trap_taken(trap_taken),
        .cycle_count(cycle_count), .instret_count(instret_count_unused)
`ifdef CPU_CACHED_SOC
        , .uart_rx_pin(1'b1), .icache_hits(icache_hits_unused), .icache_misses(icache_misses_unused),
        .dcache_hits(dcache_hits_unused), .dcache_misses(dcache_misses_unused),
        .external_req_valid(external_valid_unused), .external_req_ready(1'b1),
        .external_req_write(external_write_unused), .external_req_addr(external_addr_unused),
        .external_req_wdata(external_data_unused), .external_req_wstrb(external_strobe_unused),
        .external_rsp_valid(1'b1), .external_rsp_ready(external_rsp_ready_unused),
        .external_rsp_rdata(256'd0), .external_rsp_error(1'b1),
        .accelerator_req_valid(accelerator_valid_unused), .accelerator_req_ready(1'b1),
        .accelerator_req_write(accelerator_write_unused), .accelerator_req_addr(accelerator_addr_unused),
        .accelerator_req_wdata(accelerator_data_unused), .accelerator_req_wstrb(accelerator_strobe_unused),
        .accelerator_rsp_valid(1'b1), .accelerator_rsp_ready(accelerator_rsp_ready_unused),
        .accelerator_rsp_rdata(32'd0), .accelerator_rsp_error(1'b1)
`endif
    );

endmodule
