`timescale 1ns/1ps
module tb_mtime_soc;
    reg clk = 0, rst_n = 0;
    always #5 clk = ~clk;
    wire [31:0] gpio_old, gpio_new, pc_old, pc_new;
    wire [63:0] cycle_old, cycle_new, instret_old, instret_new;
    wire trap_old, trap_new;
    reg [1023:0] image;
    reg [63:0] golden_time, next_time;
    integer cycles = 0, writes = 0, rollovers = 0, full_rollovers = 0, traps = 0;
    integer lane, masks = 0;
    rv32i_cached_soc_baseline #(.IMEM_WORDS(1024), .DMEM_WORDS(256),
        .ICACHE_SET_BITS(4), .DCACHE_SET_BITS(3)) old (
        .clk(clk), .rst_n(rst_n), .external_irq(1'b0), .uart_rx_pin(1'b1),
        .gpio_out(gpio_old), .current_pc(pc_old), .cycle_count(cycle_old),
        .instret_count(instret_old), .trap_taken(trap_old),
        .external_req_ready(1'b1), .external_rsp_valid(1'b1),
        .external_rsp_rdata(256'd0), .external_rsp_error(1'b1),
        .accelerator_req_ready(1'b1), .accelerator_rsp_valid(1'b1),
        .accelerator_rsp_rdata(32'd0), .accelerator_rsp_error(1'b1));
    rv32i_cached_soc #(.IMEM_WORDS(1024), .DMEM_WORDS(256), .SPLIT_MTIME(1),
        .ICACHE_SET_BITS(4), .DCACHE_SET_BITS(3)) dut (
        .clk(clk), .rst_n(rst_n), .external_irq(1'b0), .uart_rx_pin(1'b1),
        .gpio_out(gpio_new), .current_pc(pc_new), .cycle_count(cycle_new),
        .instret_count(instret_new), .trap_taken(trap_new),
        .external_req_ready(1'b1), .external_rsp_valid(1'b1),
        .external_rsp_rdata(256'd0), .external_rsp_error(1'b1),
        .accelerator_req_ready(1'b1), .accelerator_rsp_valid(1'b1),
        .accelerator_rsp_rdata(32'd0), .accelerator_rsp_error(1'b1));
    always @(posedge clk) begin
        if (!rst_n) golden_time = 0;
        else begin
            next_time = golden_time + 64'd1;
            if (&golden_time[31:0]) rollovers = rollovers + 1;
            if (&golden_time) full_rollovers = full_rollovers + 1;
            if (dut.word_req_valid && dut.word_req_ready && dut.device_request && dut.word_req_write &&
                (dut.word_req_addr == 32'h20000010 || dut.word_req_addr == 32'h20000014)) begin
                writes = writes + 1;
                masks = masks | (1 << dut.word_req_wstrb);
                // Addressed halves preserve old bytes even when the strobe
                // is zero. The other half still receives the 64-bit tick.
                if (dut.word_req_addr[2]) next_time[63:32] = golden_time[63:32];
                else next_time[31:0] = golden_time[31:0];
                for (lane = 0; lane < 4; lane = lane + 1)
                    if (dut.word_req_wstrb[lane])
                        next_time[dut.word_req_addr[2]*32+lane*8 +: 8] = dut.word_req_wdata[lane*8 +: 8];
            end
            golden_time = next_time;
            if (trap_new) traps = traps + 1;
        end
    end
    always @(negedge clk) if (rst_n) begin
        cycles = cycles + 1;
        if ({pc_old, cycle_old, instret_old, gpio_old, trap_old} !==
            {pc_new, cycle_new, instret_new, gpio_new, trap_new} ||
            old.mtime !== dut.mtime || old.mtimecmp !== dut.mtimecmp ||
            old.timer_irq !== dut.timer_irq || golden_time !== dut.mtime ||
            dut.timer_irq !== (golden_time >= dut.mtimecmp))
            $fatal(1, "Timer or full SoC diverged cycle=%0d old=%h new=%h golden=%h", cycles, old.mtime, dut.mtime, golden_time);
        if (gpio_new == 32'hbad) $fatal(1, "Software timer test failed PC=%h cause=%h", pc_new, dut.core.csr_mcause);
        if (gpio_new == 32'h600d) begin
            if (writes < 12 || rollovers < 2 || full_rollovers < 1 || traps != 1 ||
                (masks & 16'h800a) != 16'h800a)
                $fatal(1, "Missing software timer coverage writes=%0d rollover=%0d full=%0d traps=%0d masks=%h",
                    writes, rollovers, full_rollovers, traps, masks);
            $display("PASS: actual cached CPU timer byte/half/word stores, low and 64-bit rollover, machine timer IRQ/MRET and cycle-equivalent full SoC (%0d cycles, %0d writes)", cycles, writes);
            $finish;
        end
        if (cycles > 6000) $fatal(1, "Software timer test timed out PC=%h", pc_new);
    end
    initial begin
        if (!$value$plusargs("image=%s", image)) $fatal(1, "Missing image");
        $readmemh(image, old.memory.instruction_mem);
        $readmemh(image, dut.memory.instruction_mem);
        repeat (5) @(negedge clk);
        rst_n = 1;
    end
endmodule
