`include "src/cpu_config.vh"
`timescale 1ns/1ps

module rv32i_cached_soc #(
    parameter integer IMEM_WORDS = 4096,
    parameter integer DMEM_WORDS = 2048,
    parameter integer DATA_ROM_ENABLE = 1,
    parameter IMEM_INIT_FILE = "",
    parameter DMEM_INIT_FILE = "",
    parameter integer CLOCK_HZ = 54000000,
    parameter integer UART_BAUD = 115200,
    parameter integer UART_RX_ENABLE = 0,
    parameter integer CACHE_ENABLE = 1,
    parameter integer CACHE_FAST_RESPONSE =
`ifdef CPU_CACHE_FAST_RESPONSE
        `CPU_CACHE_FAST_RESPONSE,
`else
        0,
`endif
    parameter integer CACHE_EARLY_RESTART =
`ifdef CPU_CACHE_EARLY_RESTART
        `CPU_CACHE_EARLY_RESTART,
`else
        0,
`endif
    parameter integer CACHE_INGRESS_WRITE =
`ifdef CPU_CACHE_INGRESS_WRITE
        `CPU_CACHE_INGRESS_WRITE,
`else
        0,
`endif
    parameter integer CACHE_FAST_WRITE_RESPONSE =
`ifdef CPU_CACHE_FAST_WRITE_RESPONSE
        `CPU_CACHE_FAST_WRITE_RESPONSE,
`else
        0,
`endif
    parameter integer CACHE_REGISTER_TAGS =
`ifdef CPU_CACHE_REGISTER_TAGS
        `CPU_CACHE_REGISTER_TAGS,
`else
        0,
`endif
    parameter integer CACHE_STATISTICS =
`ifdef CPU_CACHE_STATISTICS
        `CPU_CACHE_STATISTICS,
`else
        1,
`endif
    parameter integer EARLY_LOAD_REQUEST =
`ifdef CPU_EARLY_LOAD_REQUEST
        `CPU_EARLY_LOAD_REQUEST,
`else
        0,
`endif
    parameter integer REGISTER_ICACHE_INVALIDATE =
`ifdef CPU_REGISTER_ICACHE_INVALIDATE
        `CPU_REGISTER_ICACHE_INVALIDATE,
`else
        0,
`endif
    parameter integer ICACHE_SET_BITS = 7,
    parameter integer DCACHE_SET_BITS = 6,
    parameter integer BTB_BITS = 5,
    parameter integer RAS_BITS = 3,
    parameter integer CONTROL_LOAD_GUARD = 1,
    parameter integer LOAD_USE_GUARD = 1,
    parameter integer MULTIPLY_PIPELINE = 1,
    parameter integer SPLIT_MTIME =
`ifdef CPU_SPLIT_MTIME
        `CPU_SPLIT_MTIME,
`else
        0,
`endif
    parameter integer ALIGN_MMIO_ADDRESS =
`ifdef CPU_ALIGN_MMIO_ADDRESS
        `CPU_ALIGN_MMIO_ADDRESS
`else
        0
`endif
) (
    input wire clk, rst_n, external_irq,
    output wire [31:0] gpio_out,
    output wire uart_tx_pin,
    input wire uart_rx_pin,
    output reg [7:0] uart_debug_data,
    output reg uart_debug_strobe,
    output wire [31:0] current_pc,
    output wire trap_taken,
    output wire [63:0] cycle_count, instret_count,
    output wire [31:0] icache_hits, icache_misses, dcache_hits, dcache_misses,
    output wire external_req_valid,
    input wire external_req_ready,
    output wire external_req_write,
    output wire [31:0] external_req_addr,
    output wire [255:0] external_req_wdata,
    output wire [31:0] external_req_wstrb,
    input wire external_rsp_valid,
    output wire external_rsp_ready,
    input wire [255:0] external_rsp_rdata,
    input wire external_rsp_error,
    output wire accelerator_req_valid,
    input wire accelerator_req_ready,
    output wire accelerator_req_write,
    output wire [31:0] accelerator_req_addr, accelerator_req_wdata,
    output wire [3:0] accelerator_req_wstrb,
    input wire accelerator_rsp_valid,
    output wire accelerator_rsp_ready,
    input wire [31:0] accelerator_rsp_rdata,
    input wire accelerator_rsp_error
);
    wire imem_valid, imem_request_ready, imem_response_valid, imem_consume, imem_error;
    wire fetch_flush, fence_i;
    reg fence_i_registered;
    wire instruction_invalidate = REGISTER_ICACHE_INVALIDATE != 0 ? fence_i_registered : fence_i;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) fence_i_registered <= 0;
        else fence_i_registered <= fence_i;
    end
    wire [31:0] imem_addr, imem_rdata;
    wire dmem_valid, dmem_we, dmem_ready, dmem_error;
    wire dmem_early_valid;
    wire [31:0] dmem_early_addr;
    wire [31:0] dmem_addr, dmem_wdata, dmem_rdata, debug_x10_unused;
    wire [3:0] dmem_wstrb;
    reg [31:0] gpio_reg;
    reg [63:0] mtime, mtimecmp;
    // mtime advances every cycle, so comparing all 64 bits against mtimecmp is a
    // carry chain six levels deep.  Combinationally it reached the memory
    // backend's 256-bit response enable 13 levels later (v410 seed 428: -0.001 ns).
    // A timer interrupt is an asynchronous event; sampling it one cycle later is
    // not observable to software, and mtimecmp resets to all ones so the flop
    // starts low and the interrupt stays quiet until software arms it.
    reg timer_irq_reg;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) timer_irq_reg <= 1'b0;
        else        timer_irq_reg <= (mtime >= mtimecmp);
    end
    wire timer_irq = timer_irq_reg;
    assign gpio_out = gpio_reg;
    rv32i_pipeline_core #(.WAIT_MEMORY(1), .BTB_INDEX_BITS(BTB_BITS), .RAS_BITS(RAS_BITS),
        .MULTIPLY_PIPELINE(MULTIPLY_PIPELINE), .CONTROL_LOAD_GUARD(CONTROL_LOAD_GUARD),
        .LOAD_USE_GUARD(LOAD_USE_GUARD), .EARLY_LOAD_REQUEST(EARLY_LOAD_REQUEST)) core (
        .clk(clk), .rst_n(rst_n), .imem_valid(imem_valid), .imem_addr(imem_addr),
        .imem_rdata(imem_rdata), .imem_req_ready(imem_request_ready), .imem_ready(imem_response_valid),
        .imem_error(imem_error), .imem_consume(imem_consume), .fetch_flush(fetch_flush), .fence_i(fence_i),
        .dmem_valid(dmem_valid), .dmem_we(dmem_we), .dmem_addr(dmem_addr),
        .dmem_wdata(dmem_wdata), .dmem_wstrb(dmem_wstrb), .dmem_rdata(dmem_rdata),
        .dmem_ready(dmem_ready), .dmem_error(dmem_error), .irq_timer(timer_irq), .irq_external(external_irq),
        .dmem_early_valid(dmem_early_valid), .dmem_early_addr(dmem_early_addr),
        .current_pc(current_pc), .debug_x10(debug_x10_unused), .trap_taken(trap_taken),
        .cycle_count(cycle_count), .instret_count(instret_count)
    );
    wire word_req_valid, word_req_ready, word_req_write, word_rsp_valid, word_rsp_ready, word_rsp_error;
    wire [31:0] word_req_addr, word_req_wdata, word_rsp_rdata;
    wire [3:0] word_req_wstrb;
    cpu_data_port #(.EARLY_LOAD_REQUEST(EARLY_LOAD_REQUEST)) data_port (
        .clk(clk), .rst_n(rst_n), .cpu_valid(dmem_valid), .cpu_write(dmem_we),
        .cpu_addr(dmem_addr), .cpu_wdata(dmem_wdata), .cpu_wstrb(dmem_wstrb),
        .early_valid(dmem_early_valid), .early_addr(dmem_early_addr),
        .cpu_ready(dmem_ready), .cpu_error(dmem_error), .cpu_rdata(dmem_rdata),
        .req_valid(word_req_valid), .req_ready(word_req_ready), .req_write(word_req_write),
        .req_addr(word_req_addr), .req_wdata(word_req_wdata), .req_wstrb(word_req_wstrb),
        .rsp_valid(word_rsp_valid), .rsp_ready(word_rsp_ready),
        .rsp_error(word_rsp_error), .rsp_rdata(word_rsp_rdata)
    );

    wire [1:0] line_req_valid, line_req_ready, line_req_write, line_rsp_valid, line_rsp_ready;
    wire [63:0] line_req_addr, line_req_wstrb;
    wire [511:0] line_req_wdata;
    wire [255:0] line_rsp_rdata;
    wire line_rsp_error;
    two_way_cache #(.SET_BITS(ICACHE_SET_BITS), .READ_ONLY(1),
        .FAST_RESPONSE(CACHE_FAST_RESPONSE), .EARLY_RESTART(CACHE_EARLY_RESTART),
        .REGISTER_TAG_LOOKUP(CACHE_REGISTER_TAGS), .STATISTICS_ENABLE(CACHE_STATISTICS)) instruction_cache (
        .clk(clk), .rst_n(rst_n), .invalidate(instruction_invalidate),
        .req_valid(imem_valid), .req_ready(imem_request_ready), .req_addr(imem_addr),
        .req_write(1'b0), .req_wdata(32'd0), .req_wstrb(4'd0), .req_cacheable(CACHE_ENABLE != 0),
        .rsp_valid(imem_response_valid), .rsp_ready(imem_consume), .rsp_rdata(imem_rdata), .rsp_error(imem_error),
        .mem_req_valid(line_req_valid[0]), .mem_req_ready(line_req_ready[0]),
        .mem_req_addr(line_req_addr[31:0]), .mem_req_write(line_req_write[0]),
        .mem_req_wdata(line_req_wdata[255:0]), .mem_req_wstrb(line_req_wstrb[31:0]),
        .mem_rsp_valid(line_rsp_valid[0]), .mem_rsp_ready(line_rsp_ready[0]),
        .mem_rsp_rdata(line_rsp_rdata), .mem_rsp_error(line_rsp_error),
        .hits(icache_hits), .misses(icache_misses)
    );
    // Early reads are ordinary memory; only the held MEM request can be MMIO.
    wire [31:0] device_address = EARLY_LOAD_REQUEST != 0 ? dmem_addr : word_req_addr;
    wire device_eligible = EARLY_LOAD_REQUEST == 0 || dmem_valid;
    wire device_request = device_eligible && device_address[31:16] == 16'h2000;
    wire accelerator_request = device_eligible && device_address[31:16] == 16'h2100;
    // The CPU preserves byte offsets and supplies shifted byte strobes.
    // Only device register decoding uses an aligned word address: the cache,
    // external memory and accelerator retain their original request address.
    // Only the held MEM request can access devices. Decode its registered
    // address directly, keeping speculative EX address arithmetic off the
    // cache-invalidation -> memory-stall -> frontend control path.
    wire [31:0] device_word_addr = ALIGN_MMIO_ADDRESS != 0 ?
        {device_address[31:2], 2'b00} : device_address;
    reg [1:0] word_destination;
    wire dc_req_ready, dc_rsp_valid, dc_rsp_error;
    wire [31:0] dc_rsp_rdata;
    reg device_rsp_valid, device_rsp_error;
    reg [31:0] device_rsp_data;
    wire device_req_ready = !device_rsp_valid;
    assign word_req_ready = device_request ? device_req_ready :
                            accelerator_request ? accelerator_req_ready : dc_req_ready;
    assign word_rsp_valid = word_destination == 1 ? device_rsp_valid :
                            word_destination == 2 ? accelerator_rsp_valid : dc_rsp_valid;
    assign word_rsp_error = word_destination == 1 ? device_rsp_error :
                            word_destination == 2 ? accelerator_rsp_error : dc_rsp_error;
    assign word_rsp_rdata = word_destination == 1 ? device_rsp_data :
                            word_destination == 2 ? accelerator_rsp_rdata : dc_rsp_rdata;
    assign accelerator_req_valid = word_req_valid && accelerator_request;
    assign accelerator_req_addr = word_req_addr;
    assign accelerator_req_write = word_req_write;
    assign accelerator_req_wdata = word_req_wdata;
    assign accelerator_req_wstrb = word_req_wstrb;
    assign accelerator_rsp_ready = word_destination == 2 && word_rsp_ready;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) word_destination <= 0;
        else if (word_req_valid && word_req_ready)
            word_destination <= device_request ? 1 : accelerator_request ? 2 : 0;
    end
    // Writes from DMA/accelerator must be followed by this invalidation command
    // before the CPU reads shared tensors through the data cache.
    wire invalidate_data = word_req_valid && device_req_ready && device_request && word_req_write &&
                           device_word_addr == 32'h20000030 && word_req_wstrb[0] && word_req_wdata[0];
    two_way_cache #(.SET_BITS(DCACHE_SET_BITS), .FAST_RESPONSE(CACHE_FAST_RESPONSE),
        .EARLY_RESTART(CACHE_EARLY_RESTART), .INGRESS_WRITE_REQUEST(CACHE_INGRESS_WRITE),
        .FAST_WRITE_RESPONSE(CACHE_FAST_WRITE_RESPONSE),
        .REGISTER_TAG_LOOKUP(CACHE_REGISTER_TAGS), .STATISTICS_ENABLE(CACHE_STATISTICS)) data_cache (
        .clk(clk), .rst_n(rst_n), .invalidate(invalidate_data),
        .req_valid(word_req_valid && !device_request && !accelerator_request), .req_ready(dc_req_ready),
        .req_addr(word_req_addr), .req_write(word_req_write), .req_wdata(word_req_wdata),
        .req_wstrb(word_req_wstrb), .req_cacheable(CACHE_ENABLE != 0),
        .rsp_valid(dc_rsp_valid), .rsp_ready(word_destination == 0 && word_rsp_ready),
        .rsp_rdata(dc_rsp_rdata), .rsp_error(dc_rsp_error),
        .mem_req_valid(line_req_valid[1]), .mem_req_ready(line_req_ready[1]),
        .mem_req_addr(line_req_addr[63:32]), .mem_req_write(line_req_write[1]),
        .mem_req_wdata(line_req_wdata[511:256]), .mem_req_wstrb(line_req_wstrb[63:32]),
        .mem_rsp_valid(line_rsp_valid[1]), .mem_rsp_ready(line_rsp_ready[1]),
        .mem_rsp_rdata(line_rsp_rdata), .mem_rsp_error(line_rsp_error),
        .hits(dcache_hits), .misses(dcache_misses)
    );

    wire backing_req_valid, backing_req_ready, backing_req_write, backing_rsp_valid, backing_rsp_ready, backing_rsp_error;
    wire [31:0] backing_req_addr, backing_req_wstrb;
    wire [255:0] backing_req_wdata, backing_rsp_rdata;
    line_port_arbiter #(.CLIENTS(2)) arbiter (
        .clk(clk), .rst_n(rst_n), .req_valid(line_req_valid), .req_ready(line_req_ready),
        .req_write(line_req_write), .req_addr(line_req_addr), .req_wdata(line_req_wdata), .req_wstrb(line_req_wstrb),
        .rsp_valid(line_rsp_valid), .rsp_ready(line_rsp_ready), .rsp_rdata(line_rsp_rdata), .rsp_error(line_rsp_error),
        .master_req_valid(backing_req_valid), .master_req_ready(backing_req_ready),
        .master_req_write(backing_req_write), .master_req_addr(backing_req_addr),
        .master_req_wdata(backing_req_wdata), .master_req_wstrb(backing_req_wstrb),
        .master_rsp_valid(backing_rsp_valid), .master_rsp_ready(backing_rsp_ready),
        .master_rsp_rdata(backing_rsp_rdata), .master_rsp_error(backing_rsp_error)
    );
    memory_line_backend #(.ROM_WORDS(IMEM_WORDS), .RAM_WORDS(DMEM_WORDS),
        .FAST_WRITE_RESPONSE(CACHE_FAST_WRITE_RESPONSE),
        .ROM_INIT_FILE(IMEM_INIT_FILE), .RAM_INIT_FILE(DMEM_INIT_FILE)) memory (
        .clk(clk), .rst_n(rst_n), .req_valid(backing_req_valid), .req_ready(backing_req_ready),
        .req_write(backing_req_write), .req_addr(backing_req_addr),
        .req_wdata(backing_req_wdata), .req_wstrb(backing_req_wstrb),
        .rsp_valid(backing_rsp_valid), .rsp_ready(backing_rsp_ready),
        .rsp_rdata(backing_rsp_rdata), .rsp_error(backing_rsp_error),
        .external_req_valid(external_req_valid), .external_req_ready(external_req_ready),
        .external_req_write(external_req_write), .external_req_addr(external_req_addr),
        .external_req_wdata(external_req_wdata), .external_req_wstrb(external_req_wstrb),
        .external_rsp_valid(external_rsp_valid), .external_rsp_ready(external_rsp_ready),
        .external_rsp_rdata(external_rsp_rdata), .external_rsp_error(external_rsp_error)
    );

    function [31:0] merge_bytes;
        input [31:0] old_value, new_value;
        input [3:0] mask;
        integer byte_number;
        begin
            merge_bytes = old_value;
            for (byte_number = 0; byte_number < 4; byte_number = byte_number + 1)
                if (mask[byte_number]) merge_bytes[byte_number*8 +: 8] = new_value[byte_number*8 +: 8];
        end
    endfunction
    wire uart_ready, uart_busy_unused;
    wire [7:0] uart_rx_data;
    wire uart_rx_valid, uart_rx_framing;
    generate if (UART_RX_ENABLE != 0) begin : receive_uart
        uart_rx #(.CLOCK_HZ(CLOCK_HZ), .BAUD_RATE(UART_BAUD)) receiver (
            .clk(clk), .rst_n(rst_n), .rx(uart_rx_pin), .data(uart_rx_data),
            .valid(uart_rx_valid), .framing_error(uart_rx_framing)
        );
    end else begin : no_receive_uart
        assign uart_rx_data = 0;
        assign uart_rx_valid = 0;
        assign uart_rx_framing = 0;
    end endgenerate
    reg [7:0] uart_fifo [0:63];
    reg [5:0] uart_write_pointer, uart_read_pointer;
    reg [6:0] uart_fifo_count;
    reg [31:0] uart_receive_errors;
    wire uart_receive_available = uart_fifo_count != 0;
    wire uart_fifo_pop = word_req_valid && word_req_ready && device_request &&
                        !word_req_write && device_word_addr == 32'h2000000c && uart_receive_available;
    wire uart_fifo_push = uart_rx_valid && (uart_fifo_count < 64 || uart_fifo_pop);
    always @(posedge clk) begin
        if (uart_fifo_push) uart_fifo[uart_write_pointer] <= uart_rx_data;
    end
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            uart_write_pointer <= 0; uart_read_pointer <= 0; uart_fifo_count <= 0;
            uart_receive_errors <= 0;
        end else begin
            if (uart_fifo_push) uart_write_pointer <= uart_write_pointer + 1'b1;
            if (uart_fifo_pop) uart_read_pointer <= uart_read_pointer + 1'b1;
            case ({uart_fifo_push, uart_fifo_pop})
                2'b10: uart_fifo_count <= uart_fifo_count + 1'b1;
                2'b01: uart_fifo_count <= uart_fifo_count - 1'b1;
                default: uart_fifo_count <= uart_fifo_count;
            endcase
            if (uart_rx_framing || (uart_rx_valid && !uart_fifo_push))
                uart_receive_errors <= uart_receive_errors + 1'b1;
            if (word_req_valid && word_req_ready && device_request && word_req_write &&
                device_word_addr == 32'h20000034 && word_req_wstrb[0] && word_req_wdata[0])
                uart_receive_errors <= 0;
        end
    end
    uart_tx #(.CLOCK_HZ(CLOCK_HZ), .BAUD_RATE(UART_BAUD)) uart (
        .clk(clk), .rst_n(rst_n), .data(uart_debug_data), .valid(uart_debug_strobe),
        .ready(uart_ready), .tx(uart_tx_pin), .busy(uart_busy_unused)
    );
    // Preserve accepted MMIO write timing and the original half-word priority:
    // a partial write preserves untouched bytes of the old addressed half,
    // while the opposite half still performs its ordinary counter update.
    wire mtime_write = word_req_valid && word_req_ready && device_request && word_req_write;
    wire mtime_write_low = mtime_write && device_word_addr == 32'h20000010;
    wire mtime_write_high = mtime_write && device_word_addr == 32'h20000014;
    // Eight independent byte counters bound the arithmetic after a late MMIO
    // write decision. Carry predicates use only the old architectural timer.
    // A write freezes every unselected byte of its addressed 32-bit half,
    // including a zero-strobe write; the opposite half still increments.
    genvar byte_index;
    generate if (SPLIT_MTIME != 0) begin : split_mtime
        for (byte_index = 0; byte_index < 8; byte_index = byte_index + 1) begin : counter_byte
            wire write_half = byte_index < 4 ? mtime_write_low : mtime_write_high;
            wire carry_in;
            if (byte_index == 0) begin : first_byte
                assign carry_in = 1'b1;
            end else begin : upper_byte
                assign carry_in = &mtime[byte_index*8-1:0];
            end
            always @(posedge clk or negedge rst_n) begin
                if (!rst_n)
                    mtime[byte_index*8 +: 8] <= 8'd0;
                else if (write_half) begin
                    if (word_req_wstrb[byte_index % 4])
                        mtime[byte_index*8 +: 8] <= word_req_wdata[(byte_index % 4)*8 +: 8];
                end else if (carry_in)
                    mtime[byte_index*8 +: 8] <= mtime[byte_index*8 +: 8] + 8'd1;
            end
        end
    end else begin : original_mtime
        always @(posedge clk or negedge rst_n) begin
            if (!rst_n) mtime <= 0;
            else begin
                mtime <= mtime + 1'b1;
                if (mtime_write_low)
                    mtime[31:0] <= merge_bytes(mtime[31:0], word_req_wdata, word_req_wstrb);
                if (mtime_write_high)
                    mtime[63:32] <= merge_bytes(mtime[63:32], word_req_wdata, word_req_wstrb);
            end
        end
    end endgenerate
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            gpio_reg <= 0;
            mtimecmp <= 64'hffffffffffffffff;
            uart_debug_data <= 0;
            uart_debug_strobe <= 0;
            device_rsp_valid <= 0;
            device_rsp_error <= 0;
            device_rsp_data <= 0;
        end else begin
            uart_debug_strobe <= 0;
            if (device_rsp_valid && word_destination == 1 && word_rsp_ready) device_rsp_valid <= 0;
            if (word_req_valid && word_req_ready && device_request) begin
                device_rsp_valid <= 1;
                device_rsp_error <= 0;
                device_rsp_data <= 0;
                if (word_req_write) case (device_word_addr)
                    32'h20000000: gpio_reg <= merge_bytes(gpio_reg, word_req_wdata, word_req_wstrb);
                    32'h20000004: if (word_req_wstrb[0] && uart_ready) begin
                        uart_debug_data <= word_req_wdata[7:0];
                        uart_debug_strobe <= 1;
                    end else if (word_req_wstrb[0]) device_rsp_error <= 1;
                    32'h20000010: device_rsp_error <= 0;
                    32'h20000014: device_rsp_error <= 0;
                    32'h20000018: mtimecmp[31:0] <= merge_bytes(mtimecmp[31:0], word_req_wdata, word_req_wstrb);
                    32'h2000001c: mtimecmp[63:32] <= merge_bytes(mtimecmp[63:32], word_req_wdata, word_req_wstrb);
                    32'h20000030: device_rsp_error <= 0;
                    32'h20000034: device_rsp_error <= 0;
                    default: device_rsp_error <= 1;
                endcase
                else case (device_word_addr)
                    32'h20000000: device_rsp_data <= gpio_reg;
                    32'h20000004: device_rsp_data <= {24'd0, uart_debug_data};
                    32'h20000008: device_rsp_data <= {30'd0, uart_receive_available, uart_ready};
                    32'h2000000c: if (uart_receive_available)
                        device_rsp_data <= {24'd0, uart_fifo[uart_read_pointer]};
                        else device_rsp_error <= 1;
                    32'h20000010: device_rsp_data <= mtime[31:0];
                    32'h20000014: device_rsp_data <= mtime[63:32];
                    32'h20000018: device_rsp_data <= mtimecmp[31:0];
                    32'h2000001c: device_rsp_data <= mtimecmp[63:32];
                    32'h20000020: device_rsp_data <= icache_hits;
                    32'h20000024: device_rsp_data <= icache_misses;
                    32'h20000028: device_rsp_data <= dcache_hits;
                    32'h2000002c: device_rsp_data <= dcache_misses;
                    32'h20000030: device_rsp_data <= 0;
                    32'h20000034: device_rsp_data <= uart_receive_errors;
                    default: device_rsp_error <= 1;
                endcase
            end
        end
    end
endmodule

