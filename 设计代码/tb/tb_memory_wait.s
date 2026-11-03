`timescale 1ns/1ps

module tb_memory_wait;
    reg clk = 0, rst_n = 0, irq = 0;
    wire imem_valid, imem_consume, fetch_flush, fence_i, dmem_valid, dmem_we;
    wire [31:0] imem_addr, dmem_addr, dmem_wdata, debug_x10;
    wire [3:0] dmem_wstrb;
    reg i_pending = 0, i_rsp_valid = 0, i_error = 0;
    reg [31:0] i_address, i_data;
    integer i_delay;
    wire i_req_ready = !i_pending || (i_rsp_valid && imem_consume);
    wire d_req_valid, d_req_write, d_rsp_ready, d_ready, d_error;
    wire [31:0] d_req_addr, d_req_wdata, d_rdata;
    wire [3:0] d_req_wstrb;
    reg d_pending = 0, d_grant = 0, d_rsp_valid = 0, d_rsp_error = 0;
    wire early_valid;
    wire [31:0] early_addr;
`ifndef CPU_EARLY_LOAD_PORTS
    assign early_valid = 0;
    assign early_addr = 0;
`endif
`ifdef CPU_EARLY_LOAD_REQUEST
    localparam EARLY_LOAD_ENABLE = `CPU_EARLY_LOAD_REQUEST;
`else
    localparam EARLY_LOAD_ENABLE = 0;
`endif
    // Force acceptance of the invalid early load to cover its precise MEM fault.
    wire d_req_ready = d_grant || (EARLY_LOAD_ENABLE != 0 && early_valid &&
        early_addr == 32'h1000fffc && !d_pending && !d_rsp_valid);
    reg [31:0] d_address, d_data, d_rsp_data;
    reg [3:0] d_mask;
    reg d_write;
    integer d_delay;
    wire trap_taken;
    reg [31:0] instructions [0:2047];
    reg [31:0] memory [0:255];
    integer i, b, traps = 0, fence_count = 0, accepted_stores = 0;
    integer early_loads = 0, early_faults = 0;

    rv32i_pipeline_core #(.WAIT_MEMORY(1), .RAS_BITS(3), .CONTROL_LOAD_GUARD(1)) dut (
        .clk(clk), .rst_n(rst_n), .imem_valid(imem_valid), .imem_addr(imem_addr),
        .imem_rdata(i_data), .imem_req_ready(i_req_ready), .imem_ready(i_rsp_valid),
        .imem_error(i_error), .imem_consume(imem_consume), .fetch_flush(fetch_flush), .fence_i(fence_i),
        .dmem_valid(dmem_valid), .dmem_we(dmem_we), .dmem_addr(dmem_addr),
        .dmem_wdata(dmem_wdata), .dmem_wstrb(dmem_wstrb), .dmem_rdata(d_rdata),
        .dmem_ready(d_ready), .dmem_error(d_error), .irq_timer(1'b0), .irq_external(irq),
`ifdef CPU_EARLY_LOAD_PORTS
        .dmem_early_valid(early_valid), .dmem_early_addr(early_addr),
`endif
        .current_pc(), .debug_x10(debug_x10), .trap_taken(trap_taken), .cycle_count(), .instret_count()
    );
    cpu_data_port port (
        .clk(clk), .rst_n(rst_n), .cpu_valid(dmem_valid), .cpu_write(dmem_we),
        .cpu_addr(dmem_addr), .cpu_wdata(dmem_wdata), .cpu_wstrb(dmem_wstrb),
        .early_valid(early_valid), .early_addr(early_addr),
        .cpu_ready(d_ready), .cpu_error(d_error), .cpu_rdata(d_rdata),
        .req_valid(d_req_valid), .req_ready(d_req_ready), .req_write(d_req_write),
        .req_addr(d_req_addr), .req_wdata(d_req_wdata), .req_wstrb(d_req_wstrb),
        .rsp_valid(d_rsp_valid), .rsp_ready(d_rsp_ready), .rsp_error(d_rsp_error), .rsp_rdata(d_rsp_data)
    );
    always #5 clk = ~clk;
    always @(posedge clk) if (rst_n) begin
        if (i_rsp_valid && imem_consume) begin
            i_rsp_valid <= 0;
            i_pending <= 0;
        end
        if (imem_valid && i_req_ready) begin
            i_pending <= 1;
            i_address <= imem_addr;
            i_delay <= $urandom_range(1, 11);
        end else if (i_pending && !i_rsp_valid) begin
            if (i_delay > 0) i_delay <= i_delay - 1;
            else begin
                i_rsp_valid <= 1;
                i_error <= i_address >= 8192;
                i_data <= i_address < 8192 ? {instructions[i_address[12:2]][7:0],
                    instructions[i_address[12:2]][15:8], instructions[i_address[12:2]][23:16],
                    instructions[i_address[12:2]][31:24]} : 0;
            end
        end
        d_grant <= !d_pending && !d_rsp_valid && $urandom_range(0, 2) != 0;
        if (d_req_valid && d_req_ready) begin
            if (d_pending || d_rsp_valid) $fatal(1, "Duplicate MEM request");
            d_pending <= 1;
            d_grant <= 0;
            d_address <= d_req_addr;
            d_data <= d_req_wdata;
            d_mask <= d_req_wstrb;
            d_write <= d_req_write;
            d_delay <= $urandom_range(12, 35);
            if (d_req_write) accepted_stores <= accepted_stores + 1;
            if (!dmem_valid) begin
                early_loads <= early_loads + 1;
                if (d_req_addr == 32'h1000fffc) early_faults <= early_faults + 1;
            end
            if (d_req_write && d_req_addr == 32'h10000004) irq <= 1;
            if (d_req_write && d_req_addr == 32'h10000020)
                $fatal(1, "Wrong-path store reached memory");
        end
        if (d_pending) begin
            if (d_delay > 0) d_delay <= d_delay - 1;
            else begin
                d_pending <= 0;
                d_rsp_valid <= 1;
                d_rsp_error <= d_address[31:10] != (32'h10000000 >> 10);
                d_rsp_data <= memory[d_address[9:2]];
                if (d_write && d_address[31:10] == (32'h10000000 >> 10))
                    for (b = 0; b < 4; b = b + 1)
                        if (d_mask[b]) memory[d_address[9:2]][b*8 +: 8] <= d_data[b*8 +: 8];
            end
        end
        if (d_rsp_valid && d_rsp_ready) d_rsp_valid <= 0;
        if (trap_taken) begin
            traps <= traps + 1;
            if (dut.csr_mcause[31]) irq <= 0;
        end
        if (fence_i) fence_count <= fence_count + 1;
    end
    initial begin
        for (i = 0; i < 2048; i = i + 1) instructions[i] = 32'h13000000;
        for (i = 0; i < 256; i = i + 1) memory[i] = 0;
        $readmemh("build/memory_wait_test.hex", instructions);
        #12 rst_n = 1;
        repeat (20000) begin
            @(negedge clk);
            if (debug_x10 == 32'hbad) $fatal(1, "Wait-memory architectural result failed at pc=%h", imem_addr);
            if (debug_x10 == 32'h600d) begin
                if (EARLY_LOAD_ENABLE != 0 && (early_loads == 0 || early_faults != 1))
                    $fatal(1, "Missing early-load/error coverage loads=%0d faults=%0d", early_loads, early_faults);
                if (traps != 4 || fence_count != 1 || accepted_stores != 4 ||
                    memory[0] !== 32'h00008525 || memory[1] !== 79)
                    $fatal(1, "Wait-memory coverage/result failed traps=%0d fences=%0d stores=%0d data=%h/%h",
                        traps, fence_count, accepted_stores, memory[0], memory[1]);
                $display("PASS: wait-memory load/branch hazards, single stores, wrong paths, FENCE.I, precise I/L/S access errors and IRQ during a stalled store");
                $finish;
            end
        end
        $fatal(1, "Wait-memory test timed out at pc=%h", imem_addr);
    end
endmodule
