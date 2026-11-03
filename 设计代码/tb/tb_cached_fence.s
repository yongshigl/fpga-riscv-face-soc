`timescale 1ns/1ps

module tb_cached_fence;
    reg clk = 0, rst_n = 0;
    always #5 clk = ~clk;
    wire trap_taken;
    wire [31:0] icache_hits, icache_misses, dcache_hits, dcache_misses;
    reg [1023:0] image_path;
    integer run, cycles, fences = 0;
    integer instruction_requests = 0, instruction_refills = 0, cached_store_updates = 0;
    localparam integer CONTROL_LOAD_GUARD =
`ifdef CPU_CONTROL_LOAD_GUARD
        `CPU_CONTROL_LOAD_GUARD;
`else
        1;
`endif
    localparam integer LOAD_USE_GUARD =
`ifdef CPU_LOAD_USE_GUARD
        `CPU_LOAD_USE_GUARD;
`else
        1;
`endif
    rv32i_cached_soc #(.IMEM_WORDS(1024), .DMEM_WORDS(256),
        .ICACHE_SET_BITS(4), .DCACHE_SET_BITS(3),
        .CONTROL_LOAD_GUARD(CONTROL_LOAD_GUARD), .LOAD_USE_GUARD(LOAD_USE_GUARD)) dut (
        .clk(clk), .rst_n(rst_n), .external_irq(1'b0), .uart_rx_pin(1'b1),
        .trap_taken(trap_taken), .icache_hits(icache_hits), .icache_misses(icache_misses),
        .dcache_hits(dcache_hits), .dcache_misses(dcache_misses),
        .external_req_ready(1'b1), .external_rsp_valid(1'b1),
        .external_rsp_rdata(256'd0), .external_rsp_error(1'b1),
        .accelerator_req_ready(1'b1), .accelerator_rsp_valid(1'b1),
        .accelerator_rsp_rdata(32'd0), .accelerator_rsp_error(1'b1)
    );
    always @(posedge clk) if (rst_n && dut.fence_i) fences <= fences + 1;
    // Count actual port traffic and cached writes independently of optional MMIO counters.
    always @(posedge clk) begin
        if (!rst_n) begin
            instruction_requests <= 0;
            instruction_refills <= 0;
            cached_store_updates <= 0;
        end else begin
            if (dut.imem_valid && dut.imem_request_ready)
                instruction_requests <= instruction_requests + 1;
            if (dut.line_req_valid[0] && dut.line_req_ready[0] && !dut.line_req_write[0])
                instruction_refills <= instruction_refills + 1;
            if (dut.data_cache.data_write_valid && dut.data_cache.write_request &&
                dut.data_cache.state == dut.data_cache.MEMORY_WAIT)
                cached_store_updates <= cached_store_updates + 1;
            if (dut.CACHE_STATISTICS == 0 && {icache_hits, icache_misses, dcache_hits, dcache_misses} !== 128'd0)
                $fatal(1, "Disabled MMIO cache counters did not remain zero");
        end
    end
    initial begin
        if (!$value$plusargs("image=%s", image_path)) $fatal(1, "Missing instruction image");
        $readmemh(image_path, dut.memory.instruction_mem);
        for (run = 0; run < 2; run = run + 1) begin
            @(negedge clk); rst_n = 0;
            repeat (5) @(negedge clk);
            rst_n = 1; cycles = 0;
            while (!(dut.core.regs_valid[10] && dut.core.regs[10] == 32'h600d) && cycles < 6000) begin
                @(negedge clk); cycles = cycles + 1;
                if (trap_taken || (dut.core.regs_valid[10] && dut.core.regs[10] == 32'hbad))
                    $fatal(1, "Cached FENCE.I failed at fetch PC %h cause %h", dut.imem_addr, dut.core.csr_mcause);
            end
            if (cycles == 6000 || instruction_requests - instruction_refills < 10 || instruction_refills < 4 ||
                cached_store_updates == 0 || (dut.CACHE_STATISTICS != 0 &&
                (icache_hits < 10 || icache_misses < 4 || dcache_hits == 0)) ||
                fences != (run + 1) * 3 || dut.memory.data_mem[0] !== 32'hffd50513)
                $fatal(1, "Missing cache/fence coverage: run=%0d cycles=%0d hits=%0d misses=%0d dhits=%0d fences=%0d",
                    run, cycles, icache_hits, icache_misses, dcache_hits, fences);
        end
        $display("PASS: cached self-modifying RAM code, resident old instructions, three FENCE.I barriers, masked backing writes and warm reset (guards=%0d/%0d stats=%0d requests=%0d refills=%0d cached_store_updates=%0d)",
            dut.CONTROL_LOAD_GUARD, dut.LOAD_USE_GUARD, dut.CACHE_STATISTICS,
            instruction_requests, instruction_refills, cached_store_updates);
        $finish;
    end
endmodule
