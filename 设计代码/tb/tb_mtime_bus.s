`timescale 1ns/1ps
module tb_mtime_bus;
    reg clk = 0, rst_n = 0;
    always #5 clk = ~clk;
    reg request = 0, write = 0;
    reg [31:0] addr = 0, data = 0;
    reg [3:0] strobe = 0;
    reg [63:0] golden_time = 0, next_time;
    reg [31:0] random_bits = 32'h78319ace;
    integer byte_boundary;
    reg [63:0] boundary_value;
    integer i, lane, cycles = 0, transactions = 0, low_collision = 0, high_collision = 0, irq_edges = 0;
    reg irq_last = 0;
    rv32i_cached_soc_baseline #(.IMEM_WORDS(64), .DMEM_WORDS(64),
        .ICACHE_SET_BITS(2), .DCACHE_SET_BITS(2)) old (
        .clk(clk), .rst_n(rst_n), .external_irq(1'b0), .uart_rx_pin(1'b1),
        .external_req_ready(1'b1), .external_rsp_valid(1'b1),
        .external_rsp_rdata(256'd0), .external_rsp_error(1'b1),
        .accelerator_req_ready(1'b1), .accelerator_rsp_valid(1'b1),
        .accelerator_rsp_rdata(32'd0), .accelerator_rsp_error(1'b1));
    rv32i_cached_soc #(.IMEM_WORDS(64), .DMEM_WORDS(64), .SPLIT_MTIME(1),
        .ICACHE_SET_BITS(2), .DCACHE_SET_BITS(2)) dut (
        .clk(clk), .rst_n(rst_n), .external_irq(1'b0), .uart_rx_pin(1'b1),
        .external_req_ready(1'b1), .external_rsp_valid(1'b1),
        .external_rsp_rdata(256'd0), .external_rsp_error(1'b1),
        .accelerator_req_ready(1'b1), .accelerator_rsp_valid(1'b1),
        .accelerator_rsp_rdata(32'd0), .accelerator_rsp_error(1'b1));
    // Drive the CPU's documented held data bus, including its transaction
    // adapter and device decoder. No timer state or arithmetic is forced.
    initial begin
        force old.imem_valid = 0;
        force dut.imem_valid = 0;
        force old.dmem_early_valid = 0;
        force dut.dmem_early_valid = 0;
        force old.dmem_valid = request;
        force dut.dmem_valid = request;
        force old.dmem_we = write;
        force dut.dmem_we = write;
        force old.dmem_addr = addr;
        force dut.dmem_addr = addr;
        force old.dmem_wdata = data;
        force dut.dmem_wdata = data;
        force old.dmem_wstrb = strobe;
        force dut.dmem_wstrb = strobe;
    end
    always @(posedge clk) begin
        if (!rst_n) golden_time = 0;
        else begin
            next_time = golden_time + 64'd1;
            if (dut.word_req_valid && dut.word_req_ready && dut.device_request && dut.word_req_write &&
                (dut.word_req_addr == 32'h20000010 || dut.word_req_addr == 32'h20000014)) begin
                if (&golden_time[31:0]) begin
                    if (dut.word_req_addr[2]) high_collision = high_collision + 1;
                    else low_collision = low_collision + 1;
                end
                if (dut.word_req_addr[2]) next_time[63:32] = golden_time[63:32];
                else next_time[31:0] = golden_time[31:0];
                for (lane = 0; lane < 4; lane = lane + 1)
                    if (dut.word_req_wstrb[lane])
                        next_time[dut.word_req_addr[2]*32+lane*8 +: 8] = dut.word_req_wdata[lane*8 +: 8];
            end
            golden_time = next_time;
        end
    end
    always @(negedge clk) if (rst_n) begin
        cycles = cycles + 1;
        if (golden_time !== dut.mtime || old.mtime !== dut.mtime || old.mtimecmp !== dut.mtimecmp ||
            old.timer_irq !== dut.timer_irq || dut.timer_irq !== (golden_time >= dut.mtimecmp) ||
            {old.dmem_ready, old.dmem_error, old.dmem_rdata} !==
            {dut.dmem_ready, dut.dmem_error, dut.dmem_rdata})
            $fatal(1, "Timer MMIO mismatch cycle=%0d old=%h new=%h golden=%h", cycles, old.mtime, dut.mtime, golden_time);
        if (dut.timer_irq != irq_last) irq_edges = irq_edges + 1;
        irq_last = dut.timer_irq;
        if (cycles > 30000) $fatal(1, "Timer MMIO timeout");
    end
    task transfer;
        input is_write;
        input [31:0] address, value;
        input [3:0] lanes;
        integer timeout;
        begin
            addr = address; data = value; strobe = lanes; write = is_write; request = 1;
            timeout = 0;
            @(negedge clk);
            while (!dut.dmem_ready && timeout < 20) begin @(negedge clk); timeout = timeout + 1; end
            if (timeout == 20 || dut.dmem_error) $fatal(1, "MMIO bus transaction failed %h", address);
            @(negedge clk); request = 0;
            @(negedge clk);
            transactions = transactions + 1;
        end
    endtask
    task collision;
        input [31:0] target;
        begin
            transfer(1, 32'h20000010, 32'hfffffff8, 4'hf);
            while (dut.mtime[31:0] != 32'hffffffff) @(negedge clk);
            transfer(1, target, 32'h89abcdef, 4'h5);
        end
    endtask
    initial begin
        repeat (5) @(negedge clk); rst_n = 1;
        @(negedge clk);
        collision(32'h20000010);
        collision(32'h20000014);
        // Exercise every byte-enable mask on both halves through real MMIO.
        for (i = 0; i < 16; i = i + 1) begin
            transfer(1, 32'h20000010, 32'h12cdab34 ^ i, i[3:0]);
            transfer(1, 32'h20000014, 32'h78bcde56 ^ i, i[3:0]);
            transfer(0, 32'h20000010, 0, 0);
            transfer(0, 32'h20000014, 0, 0);
        end
        transfer(1, 32'h20000014, 0, 4'hf);
        transfer(1, 32'h20000010, 0, 4'hf);
        transfer(1, 32'h2000001c, 0, 4'hf);
        transfer(1, 32'h20000018, dut.mtime[31:0]+20, 4'hf);
        repeat (40) @(negedge clk);
        if (!dut.timer_irq) $fatal(1, "IRQ did not assert at compare equality");
        transfer(1, 32'h2000001c, -1, 4'hf);
        if (dut.timer_irq) $fatal(1, "IRQ did not clear after compare update");
        for (i = 0; i < 256; i = i + 1) begin
            random_bits = random_bits ^ (random_bits << 13);
            random_bits = random_bits ^ (random_bits >> 17);
            random_bits = random_bits ^ (random_bits << 5);
            transfer(1, 32'h20000010 + {28'd0, random_bits[9:8], 2'd0}, random_bits, random_bits[3:0]);
        end
`ifdef TEST_TIMER_BYTE_BOUNDARIES
        // Position the real counter just before every byte carry using MMIO,
        // then observe unmodified clock-by-clock equivalence across rollover.
        for (byte_boundary = 1; byte_boundary <= 8; byte_boundary = byte_boundary + 1) begin
            boundary_value = byte_boundary == 8 ? 64'hffffffffffffffd0 :
                             (64'd1 << (byte_boundary*8)) - 64'd48;
            transfer(1, 32'h20000014, boundary_value[63:32], 4'hf);
            transfer(1, 32'h20000010, boundary_value[31:0], 4'hf);
            repeat (64) @(negedge clk);
        end
        $display("PASS: all eight byte carry boundaries including full 64-bit wrap through actual MMIO");
`endif
        @(negedge clk); rst_n = 0;
        repeat (3) @(negedge clk);
        if (dut.mtime !== 0 || dut.timer_irq) $fatal(1, "Warm reset failed");
        rst_n = 1; repeat (10) @(negedge clk);
        if (low_collision < 1 || high_collision < 1 || irq_edges < 2)
            $fatal(1, "Missing boundary coverage low=%0d high=%0d irq_edges=%0d", low_collision, high_collision, irq_edges);
        $display("PASS: %0d actual timer MMIO adapter transactions, every byte mask including zero, writes on low-half overflow, IRQ equality/clear and warm reset", transactions);
        $finish;
    end
endmodule
