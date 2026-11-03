`timescale 1ns/1ps

module vision_soc #(
    parameter integer CPU_CLOCK_HZ = 81000000,
    parameter integer AI_CLOCK_HZ = 54000000,
    parameter integer MAC_PIXELS = 16,
    parameter integer AI_READ_CACHE_LINES = 2,
    parameter integer AI_DEPTHWISE_CHANNEL_OUTER = 0,
    parameter integer AI_READ_CACHE_FULL_LRU = 0,
    parameter integer ICACHE_SET_BITS = 7,
    parameter integer DCACHE_SET_BITS = 6,
    parameter integer CAMERA_ENABLE = 0,
    parameter IMEM_INIT_FILE = "mem/vision_text.hex",
    parameter DMEM_INIT_FILE = "mem/vision_data.hex"
) (
    input wire cpu_clk, cpu_rst_n, ai_clk, ai_rst_n, memory_clk, memory_rst_n,
    input wire uart_rx,
    output wire uart_tx,
    output wire [31:0] gpio, current_pc,
    output wire trap_taken,
    output wire [63:0] cycle_count, instret_count,
    output wire [7:0] accelerator_state,
    output wire accelerator_irq,
    output wire mem_req_valid,
    input wire mem_req_ready,
    output wire mem_req_write,
    output wire [31:0] mem_req_addr, mem_req_wstrb,
    output wire [255:0] mem_req_wdata,
    input wire mem_rsp_valid,
    output wire mem_rsp_ready,
    input wire [255:0] mem_rsp_rdata,
    input wire mem_rsp_error,
    output wire camera_req_valid,
    input wire camera_req_ready,
    output wire camera_req_write,
    output wire [31:0] camera_req_addr, camera_req_wdata,
    output wire [3:0] camera_req_wstrb,
    input wire camera_rsp_valid,
    output wire camera_rsp_ready,
    input wire [31:0] camera_rsp_rdata,
    input wire camera_rsp_error
);
    wire cpu_core_req_valid, cpu_core_req_ready, cpu_core_req_write;
    wire cpu_core_rsp_valid, cpu_core_rsp_ready, cpu_core_rsp_error;
    wire [31:0] cpu_core_req_addr, cpu_core_req_wstrb;
    wire [255:0] cpu_core_req_wdata, cpu_core_rsp_rdata;
    wire cpu_req_valid, cpu_req_ready, cpu_req_write, cpu_rsp_valid, cpu_rsp_ready, cpu_rsp_error;
    wire [31:0] cpu_req_addr, cpu_req_wstrb;
    wire [255:0] cpu_req_wdata, cpu_rsp_rdata;
    wire control_req_valid, control_req_ready, control_req_write, control_rsp_valid, control_rsp_ready, control_rsp_error;
    wire [31:0] control_req_addr, control_req_wdata, control_rsp_rdata;
    wire [3:0] control_req_wstrb;
    wire [7:0] uart_debug_data;
    wire uart_debug_strobe;
    wire [31:0] icache_hits, icache_misses, dcache_hits, dcache_misses;
    rv32i_cached_soc #(
        .IMEM_WORDS(8192), .DMEM_WORDS(8192), .DATA_ROM_ENABLE(1),
        .IMEM_INIT_FILE(IMEM_INIT_FILE), .DMEM_INIT_FILE(DMEM_INIT_FILE),
        .CLOCK_HZ(CPU_CLOCK_HZ), .UART_BAUD(921600), .UART_RX_ENABLE(1),
        .ICACHE_SET_BITS(ICACHE_SET_BITS), .DCACHE_SET_BITS(DCACHE_SET_BITS),
        .BTB_BITS(5), .RAS_BITS(3),
        .CONTROL_LOAD_GUARD(1), .LOAD_USE_GUARD(1), .MULTIPLY_PIPELINE(1)
    ) cpu (
        .clk(cpu_clk), .rst_n(cpu_rst_n), .external_irq(1'b0),
        .gpio_out(gpio), .uart_rx_pin(uart_rx), .uart_tx_pin(uart_tx),
        .uart_debug_data(uart_debug_data), .uart_debug_strobe(uart_debug_strobe),
        .current_pc(current_pc), .trap_taken(trap_taken),
        .cycle_count(cycle_count), .instret_count(instret_count),
        .icache_hits(icache_hits), .icache_misses(icache_misses), .dcache_hits(dcache_hits), .dcache_misses(dcache_misses),
        .external_req_valid(cpu_core_req_valid), .external_req_ready(cpu_core_req_ready),
        .external_req_write(cpu_core_req_write), .external_req_addr(cpu_core_req_addr),
        .external_req_wdata(cpu_core_req_wdata), .external_req_wstrb(cpu_core_req_wstrb),
        .external_rsp_valid(cpu_core_rsp_valid), .external_rsp_ready(cpu_core_rsp_ready),
        .external_rsp_rdata(cpu_core_rsp_rdata), .external_rsp_error(cpu_core_rsp_error),
        .accelerator_req_valid(control_req_valid), .accelerator_req_ready(control_req_ready),
        .accelerator_req_write(control_req_write), .accelerator_req_addr(control_req_addr),
        .accelerator_req_wdata(control_req_wdata), .accelerator_req_wstrb(control_req_wstrb),
        .accelerator_rsp_valid(control_rsp_valid), .accelerator_rsp_ready(control_rsp_ready),
        .accelerator_rsp_rdata(control_rsp_rdata), .accelerator_rsp_error(control_rsp_error)
    );

    wire ai_control_req_valid, ai_control_req_ready, ai_control_req_write;
    wire ai_control_rsp_valid, ai_control_rsp_ready, ai_control_rsp_error;
    wire [31:0] ai_control_req_addr, ai_control_req_wdata, ai_control_rsp_rdata;
    wire [3:0] ai_control_req_wstrb;
    wire engine_control_req_ready, engine_control_rsp_valid, engine_control_rsp_error;
    wire [31:0] engine_control_rsp_rdata;
    wire camera_request = CAMERA_ENABLE != 0 && control_req_addr[15];
    reg camera_response;
    assign camera_req_valid = control_req_valid && camera_request;
    assign camera_req_write = control_req_write;
    assign camera_req_addr = control_req_addr;
    assign camera_req_wdata = control_req_wdata;
    assign camera_req_wstrb = control_req_wstrb;
    assign camera_rsp_ready = camera_response && control_rsp_ready;
    assign control_req_ready = camera_request ? camera_req_ready : engine_control_req_ready;
    assign control_rsp_valid = camera_response ? camera_rsp_valid : engine_control_rsp_valid;
    assign control_rsp_rdata = camera_response ? camera_rsp_rdata : engine_control_rsp_rdata;
    assign control_rsp_error = camera_response ? camera_rsp_error : engine_control_rsp_error;
    always @(posedge cpu_clk or negedge cpu_rst_n) begin
        if (!cpu_rst_n) camera_response <= 0;
        else if (control_req_valid && control_req_ready) camera_response <= camera_request;
    end
    transaction_cdc #(.DATA_BITS(32)) control_crossing (
        .source_clk(cpu_clk), .source_rst_n(cpu_rst_n),
        .source_req_valid(control_req_valid && !camera_request), .source_req_ready(engine_control_req_ready),
        .source_req_write(control_req_write), .source_req_addr(control_req_addr),
        .source_req_wdata(control_req_wdata), .source_req_wstrb(control_req_wstrb),
        .source_rsp_valid(engine_control_rsp_valid), .source_rsp_ready(control_rsp_ready && !camera_response),
        .source_rsp_rdata(engine_control_rsp_rdata), .source_rsp_error(engine_control_rsp_error),
        .target_clk(ai_clk), .target_rst_n(ai_rst_n),
        .target_req_valid(ai_control_req_valid), .target_req_ready(ai_control_req_ready),
        .target_req_write(ai_control_req_write), .target_req_addr(ai_control_req_addr),
        .target_req_wdata(ai_control_req_wdata), .target_req_wstrb(ai_control_req_wstrb),
        .target_rsp_valid(ai_control_rsp_valid), .target_rsp_ready(ai_control_rsp_ready),
        .target_rsp_rdata(ai_control_rsp_rdata), .target_rsp_error(ai_control_rsp_error)
    );
    wire ai_req_valid, ai_req_ready, ai_req_write, ai_rsp_valid, ai_rsp_ready, ai_rsp_error;
    wire [31:0] ai_req_addr, ai_req_wstrb;
    wire [255:0] ai_req_wdata, ai_rsp_rdata;
    int8_accelerator #(.MAC_PIXELS(MAC_PIXELS), .READ_CACHE_LINES(AI_READ_CACHE_LINES),
        .DEPTHWISE_CHANNEL_OUTER(AI_DEPTHWISE_CHANNEL_OUTER),
        .READ_CACHE_FULL_LRU(AI_READ_CACHE_FULL_LRU)) accelerator (
        .clk(ai_clk), .rst_n(ai_rst_n),
        .control_req_valid(ai_control_req_valid), .control_req_ready(ai_control_req_ready),
        .control_req_write(ai_control_req_write), .control_req_addr(ai_control_req_addr),
        .control_req_wdata(ai_control_req_wdata), .control_req_wstrb(ai_control_req_wstrb),
        .control_rsp_valid(ai_control_rsp_valid), .control_rsp_ready(ai_control_rsp_ready),
        .control_rsp_rdata(ai_control_rsp_rdata), .control_rsp_error(ai_control_rsp_error),
        .irq(accelerator_irq), .debug_state(accelerator_state),
        .mem_req_valid(ai_req_valid), .mem_req_ready(ai_req_ready), .mem_req_write(ai_req_write),
        .mem_req_addr(ai_req_addr), .mem_req_wdata(ai_req_wdata), .mem_req_wstrb(ai_req_wstrb),
        .mem_rsp_valid(ai_rsp_valid), .mem_rsp_ready(ai_rsp_ready),
        .mem_rsp_rdata(ai_rsp_rdata), .mem_rsp_error(ai_rsp_error)
    );

    wire [1:0] req_valid, req_ready, req_write, rsp_valid, rsp_ready;
    wire [63:0] req_addr, req_wstrb;
    wire [511:0] req_wdata;
    wire [255:0] rsp_rdata;
    wire rsp_error;
    wire cdc_req_ready, cdc_rsp_valid, cdc_rsp_error;
    wire [255:0] cdc_rsp_rdata;
    reg req_pipe_valid, req_pipe_write;
    reg [31:0] req_pipe_addr;
    reg [255:0] req_pipe_wdata;
    reg [31:0] req_pipe_wstrb;
    reg rsp_pipe_valid, rsp_pipe_error;
    reg [255:0] rsp_pipe_rdata;
    assign cpu_core_req_ready = !req_pipe_valid || cdc_req_ready;
    assign cpu_req_valid = req_pipe_valid;
    assign cpu_req_ready = cdc_req_ready;
    assign cpu_req_write = req_pipe_write;
    assign cpu_req_addr = req_pipe_addr;
    assign cpu_req_wdata = req_pipe_wdata;
    assign cpu_req_wstrb = req_pipe_wstrb;
    assign cpu_core_rsp_valid = rsp_pipe_valid;
    assign cpu_core_rsp_rdata = rsp_pipe_rdata;
    assign cpu_core_rsp_error = rsp_pipe_error;
    assign cpu_rsp_ready = !rsp_pipe_valid || cpu_core_rsp_ready;
    always @(posedge cpu_clk or negedge cpu_rst_n) begin
        if (!cpu_rst_n) begin
            req_pipe_valid <= 0; req_pipe_write <= 0; req_pipe_addr <= 0;
            req_pipe_wdata <= 0; req_pipe_wstrb <= 0;
        end else if (cpu_core_req_valid && cpu_core_req_ready) begin
            req_pipe_valid <= 1;
            req_pipe_write <= cpu_core_req_write;
            req_pipe_addr <= cpu_core_req_addr;
            req_pipe_wdata <= cpu_core_req_wdata;
            req_pipe_wstrb <= cpu_core_req_wstrb;
        end else if (req_pipe_valid && cdc_req_ready) begin
            req_pipe_valid <= 0;
        end
    end
    always @(posedge cpu_clk or negedge cpu_rst_n) begin
        if (!cpu_rst_n) begin
            rsp_pipe_valid <= 0; rsp_pipe_error <= 0; rsp_pipe_rdata <= 0;
        end else if (cdc_rsp_valid && cpu_rsp_ready) begin
            rsp_pipe_valid <= 1;
            rsp_pipe_rdata <= cdc_rsp_rdata;
            rsp_pipe_error <= cdc_rsp_error;
        end else if (rsp_pipe_valid && cpu_core_rsp_ready) begin
            rsp_pipe_valid <= 0;
        end
    end
    transaction_cdc cpu_memory_crossing (
        .source_clk(cpu_clk), .source_rst_n(cpu_rst_n),
        .source_req_valid(cpu_req_valid), .source_req_ready(cdc_req_ready),
        .source_req_write(cpu_req_write), .source_req_addr(cpu_req_addr),
        .source_req_wdata(cpu_req_wdata), .source_req_wstrb(cpu_req_wstrb),
        .source_rsp_valid(cdc_rsp_valid), .source_rsp_ready(cpu_rsp_ready),
        .source_rsp_rdata(cdc_rsp_rdata), .source_rsp_error(cdc_rsp_error),
        .target_clk(memory_clk), .target_rst_n(memory_rst_n),
        .target_req_valid(req_valid[0]), .target_req_ready(req_ready[0]),
        .target_req_write(req_write[0]), .target_req_addr(req_addr[31:0]),
        .target_req_wdata(req_wdata[255:0]), .target_req_wstrb(req_wstrb[31:0]),
        .target_rsp_valid(rsp_valid[0]), .target_rsp_ready(rsp_ready[0]),
        .target_rsp_rdata(rsp_rdata), .target_rsp_error(rsp_error)
    );
    transaction_cdc accelerator_memory_crossing (
        .source_clk(ai_clk), .source_rst_n(ai_rst_n),
        .source_req_valid(ai_req_valid), .source_req_ready(ai_req_ready),
        .source_req_write(ai_req_write), .source_req_addr(ai_req_addr),
        .source_req_wdata(ai_req_wdata), .source_req_wstrb(ai_req_wstrb),
        .source_rsp_valid(ai_rsp_valid), .source_rsp_ready(ai_rsp_ready),
        .source_rsp_rdata(ai_rsp_rdata), .source_rsp_error(ai_rsp_error),
        .target_clk(memory_clk), .target_rst_n(memory_rst_n),
        .target_req_valid(req_valid[1]), .target_req_ready(req_ready[1]),
        .target_req_write(req_write[1]), .target_req_addr(req_addr[63:32]),
        .target_req_wdata(req_wdata[511:256]), .target_req_wstrb(req_wstrb[63:32]),
        .target_rsp_valid(rsp_valid[1]), .target_rsp_ready(rsp_ready[1]),
        .target_rsp_rdata(rsp_rdata), .target_rsp_error(rsp_error)
    );
    assign rsp_rdata = mem_rsp_rdata;
    assign rsp_error = mem_rsp_error;
    line_port_arbiter #(.CLIENTS(2)) arbiter (
        .clk(memory_clk), .rst_n(memory_rst_n), .req_valid(req_valid), .req_ready(req_ready),
        .req_write(req_write), .req_addr(req_addr), .req_wdata(req_wdata), .req_wstrb(req_wstrb),
        .rsp_valid(rsp_valid), .rsp_ready(rsp_ready), .rsp_rdata(rsp_rdata), .rsp_error(rsp_error),
        .master_req_valid(mem_req_valid), .master_req_ready(mem_req_ready),
        .master_req_write(mem_req_write), .master_req_addr(mem_req_addr),
        .master_req_wdata(mem_req_wdata), .master_req_wstrb(mem_req_wstrb),
        .master_rsp_valid(mem_rsp_valid), .master_rsp_ready(mem_rsp_ready),
        .master_rsp_rdata(mem_rsp_rdata), .master_rsp_error(mem_rsp_error)
    );
endmodule
