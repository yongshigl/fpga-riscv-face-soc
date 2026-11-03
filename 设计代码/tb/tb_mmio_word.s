`timescale 1ns/1ps
module tb_mmio_word #(
    parameter integer ALIGN_MMIO_ADDRESS = 1
);
    reg clk = 0, rst_n = 0, rx = 1;
    always #5 clk = ~clk;
    wire [31:0] gpio_old, gpio_new, pc_old, pc_new;
    wire [63:0] cycles_old, cycles_new, instret_old, instret_new;
    wire trap_old, trap_new, uart_strobe;
    wire [7:0] uart_data;
    reg [1023:0] image;
    reg [63:0] golden_time = 0, next_time;
    integer cycles = 0, lane, traps = 0, transmitted = 0, popped = 0, invalidates = 0;
    integer byte_masks = 0, half_masks = 0;
    rv32i_cached_soc #(.IMEM_WORDS(1024), .DMEM_WORDS(256), .SPLIT_MTIME(0), .ALIGN_MMIO_ADDRESS(ALIGN_MMIO_ADDRESS),
        .CLOCK_HZ(400), .UART_BAUD(100), .UART_RX_ENABLE(1),
        .ICACHE_SET_BITS(4), .DCACHE_SET_BITS(3)) old (
        .clk(clk), .rst_n(rst_n), .external_irq(1'b0), .uart_rx_pin(rx),
        .gpio_out(gpio_old), .current_pc(pc_old), .cycle_count(cycles_old),
        .instret_count(instret_old), .trap_taken(trap_old),
        .external_req_ready(1'b1), .external_rsp_valid(1'b1),
        .external_rsp_rdata(256'd0), .external_rsp_error(1'b1),
        .accelerator_req_ready(1'b1), .accelerator_rsp_valid(1'b1),
        .accelerator_rsp_rdata(32'd0), .accelerator_rsp_error(1'b1));
    rv32i_cached_soc #(.IMEM_WORDS(1024), .DMEM_WORDS(256), .SPLIT_MTIME(1), .ALIGN_MMIO_ADDRESS(ALIGN_MMIO_ADDRESS),
        .CLOCK_HZ(400), .UART_BAUD(100), .UART_RX_ENABLE(1),
        .ICACHE_SET_BITS(4), .DCACHE_SET_BITS(3)) dut (
        .clk(clk), .rst_n(rst_n), .external_irq(1'b0), .uart_rx_pin(rx),
        .gpio_out(gpio_new), .current_pc(pc_new), .cycle_count(cycles_new),
        .instret_count(instret_new), .trap_taken(trap_new),
        .uart_debug_strobe(uart_strobe), .uart_debug_data(uart_data),
        .external_req_ready(1'b1), .external_rsp_valid(1'b1),
        .external_rsp_rdata(256'd0), .external_rsp_error(1'b1),
        .accelerator_req_ready(1'b1), .accelerator_rsp_valid(1'b1),
        .accelerator_rsp_rdata(32'd0), .accelerator_rsp_error(1'b1));
    always @(posedge clk) begin
        if (!rst_n) golden_time = 0;
        else begin
            next_time = golden_time + 64'd1;
            if (dut.word_req_valid && dut.word_req_ready && dut.device_request && dut.word_req_write) begin
                case (dut.word_req_wstrb)
                    1,2,4,8: byte_masks = byte_masks | (1 << dut.word_req_wstrb);
                    3,12: half_masks = half_masks | (1 << dut.word_req_wstrb);
                endcase
                if (dut.device_word_addr == 32'h20000010 || dut.device_word_addr == 32'h20000014) begin
                    if (dut.device_word_addr[2]) next_time[63:32] = golden_time[63:32];
                    else next_time[31:0] = golden_time[31:0];
                    for (lane = 0; lane < 4; lane = lane + 1)
                        if (dut.word_req_wstrb[lane])
                            next_time[dut.device_word_addr[2]*32+lane*8 +: 8] = dut.word_req_wdata[lane*8 +: 8];
                end
            end
            golden_time = next_time;
            if (trap_new) traps = traps + 1;
            if (dut.uart_fifo_pop) popped = popped + 1;
            if (dut.invalidate_data) invalidates = invalidates + 1;
            if (uart_strobe) begin
                if (uart_data !== 8'h41) $fatal(1, "Unexpected UART byte %h", uart_data);
                transmitted = transmitted + 1;
            end
            if (dut.word_req_valid && !dut.dmem_valid &&
                (dut.word_req_addr[31:16] == 16'h2000 || dut.word_req_addr[31:16] == 16'h2100))
                $fatal(1, "An early speculative load reached MMIO");
        end
    end
    always @(negedge clk) if (rst_n) begin
        cycles = cycles + 1;
        if ({pc_old, cycles_old, instret_old, gpio_old, trap_old} !==
            {pc_new, cycles_new, instret_new, gpio_new, trap_new} || old.mtime !== dut.mtime ||
            old.mtimecmp !== dut.mtimecmp || dut.mtime !== golden_time ||
            old.timer_irq !== dut.timer_irq || old.uart_read_pointer !== dut.uart_read_pointer)
            $fatal(1, "Aligned MMIO/timer full SoC diverged at cycle %0d", cycles);
        if (gpio_new == 32'hbad)
            $fatal(1, "Software MMIO failed PC=%h cause=%h traps=%0d tx=%0d rx=%0d",
                pc_new, dut.core.csr_mcause, traps, transmitted, popped);
        if (gpio_new == 32'h600d) begin
            if (traps != 8 || transmitted != 1 || popped != 3 || invalidates != 1 ||
                byte_masks != 16'h0116 || half_masks != 16'h1008)
                $fatal(1, "Missing MMIO coverage traps=%0d tx=%0d pops=%0d invalidate=%0d byte=%h half=%h",
                    traps, transmitted, popped, invalidates, byte_masks, half_masks);
            $display("PASS: CPU GPIO/timer/cache-counter byte loads/stores all lanes and halfwords, 8 precise misalignment/access faults, single UART TX, 3 real serial RX pops, lane-gated invalidate and full SoC cycle equivalence (%0d cycles)",cycles);
            $finish;
        end
        if (cycles > 20000) $fatal(1, "MMIO software timeout PC=%h",pc_new);
    end
    task serial_byte;
        input [7:0] value;
        integer bit_index;
        begin
            @(negedge clk); rx = 0;
            repeat (4) @(negedge clk);
            for (bit_index = 0; bit_index < 8; bit_index = bit_index + 1) begin
                rx = value[bit_index]; repeat (4) @(negedge clk);
            end
            rx = 1; repeat (8) @(negedge clk);
        end
    endtask
    initial begin
        if (!$value$plusargs("image=%s", image)) $fatal(1, "Missing image");
        $readmemh(image, old.memory.instruction_mem);
        $readmemh(image, dut.memory.instruction_mem);
        repeat (5) @(negedge clk); rst_n = 1;
        wait (gpio_new == 32'h1234);
        serial_byte(8'h5a); serial_byte(8'hc3); serial_byte(8'h77);
    end
endmodule
