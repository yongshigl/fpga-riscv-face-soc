`timescale 1ns/1ps

module tb_bypass_wait;
    reg clk = 0, rst_n = 0;
    always #5 clk = !clk;
    wire imem_valid, imem_consume, dmem_valid, dmem_we;
    wire [31:0] imem_addr, dmem_addr, dmem_wdata, debug_x10;
    wire [3:0] dmem_wstrb;
    reg i_valid = 0;
    reg [31:0] i_data = 32'h00000013;
    wire i_ready = !i_valid || imem_consume;
    reg [31:0] instructions [0:2047];
    reg [31:0] memory [0:7];
    reg d_pending = 0;
    reg [31:0] d_address, d_data;
    reg [3:0] d_mask;
    reg d_write;
    integer d_delay = 0;
    wire d_ready = d_pending && d_delay == 0;
    wire [31:0] d_rdata = memory[d_address[4:2]];
    integer i, b, run, cycle;
    integer wb_rs1_waits = 0, wb_rs2_waits = 0, branch_waits = 0, jalr_waits = 0;
    integer stores = 0;
    wire trap_taken;

    rv32i_pipeline_core #(.WAIT_MEMORY(1)) dut (
        .clk(clk), .rst_n(rst_n), .imem_valid(imem_valid), .imem_addr(imem_addr),
        .imem_rdata(i_data), .imem_req_ready(i_ready), .imem_ready(i_valid), .imem_error(1'b0),
        .imem_consume(imem_consume), .fetch_flush(), .fence_i(),
        .dmem_valid(dmem_valid), .dmem_we(dmem_we), .dmem_addr(dmem_addr),
        .dmem_wdata(dmem_wdata), .dmem_wstrb(dmem_wstrb), .dmem_rdata(d_rdata),
        .dmem_ready(d_ready), .dmem_error(1'b0), .irq_timer(1'b0), .irq_external(1'b0),
        .current_pc(), .debug_x10(debug_x10), .trap_taken(trap_taken), .cycle_count(), .instret_count()
    );

    always @(posedge clk) begin
        if (!rst_n) begin
            i_valid <= 0;
            d_pending <= 0;
            wb_rs1_waits <= 0;
            wb_rs2_waits <= 0;
            branch_waits <= 0;
            jalr_waits <= 0;
            stores <= 0;
        end else begin
            if (i_valid && imem_consume) i_valid <= 0;
            if (imem_valid && i_ready) begin
                if (imem_addr >= 8192) $fatal(1, "Unexpected instruction address %h", imem_addr);
                i_valid <= 1;
                i_data <= {instructions[imem_addr[12:2]][7:0], instructions[imem_addr[12:2]][15:8],
                           instructions[imem_addr[12:2]][23:16], instructions[imem_addr[12:2]][31:24]};
            end
            if (dmem_valid && !d_pending) begin
                if (dmem_addr[31:5] != (32'h10000000 >> 5) || dmem_addr[4:0] == 28)
                    $fatal(1, "Wrong-path/invalid memory request %h", dmem_addr);
                d_pending <= 1;
                d_address <= dmem_addr;
                d_data <= dmem_wdata;
                d_mask <= dmem_wstrb;
                d_write <= dmem_we;
                d_delay <= $urandom_range(8, 25);
            end else if (d_pending && d_delay != 0) d_delay <= d_delay - 1;
            if (d_ready) begin
                d_pending <= 0;
                if (d_write) begin
                    stores <= stores + 1;
                    for (b = 0; b < 4; b = b + 1)
                        if (d_mask[b]) memory[d_address[4:2]][b*8 +: 8] <= d_data[b*8 +: 8];
                end
            end
            if (trap_taken) $fatal(1, "Unexpected trap during bypass test");
            if (dut.memory_stall && dut.idex_valid && dut.memwb_valid &&
                dut.memwb_reg_write && dut.memwb_rd != 0) begin
                if (dut.memwb_rd == dut.idex_rs1) wb_rs1_waits <= wb_rs1_waits + 1;
                if (dut.memwb_rd == dut.idex_rs2) wb_rs2_waits <= wb_rs2_waits + 1;
                if (dut.memwb_rd == dut.idex_rs1 && dut.idex_instr[6:0] == 7'b1100011)
                    branch_waits <= branch_waits + 1;
                if (dut.memwb_rd == dut.idex_rs1 && dut.idex_instr[6:0] == 7'b1100111)
                    jalr_waits <= jalr_waits + 1;
            end
        end
    end

    initial begin
        for (i = 0; i < 2048; i = i + 1) instructions[i] = 32'h13000000;
        for (i = 0; i < 8; i = i + 1) memory[i] = 0;
        memory[0] = 31;
        memory[1] = 47;
        $readmemh("build/bypass_wait_test.hex", instructions);
        for (run = 0; run < 2; run = run + 1) begin
            #12 rst_n = 1;
            begin : wait_for_result
                for (cycle = 0; cycle < 4000; cycle = cycle + 1) begin
                    @(negedge clk);
                    if (debug_x10 == 32'hbad) $fatal(1, "Architectural bypass result failed at %h", imem_addr);
                    if (debug_x10 == 32'h600d) begin
                        if (wb_rs1_waits < 3 || wb_rs2_waits < 1 || branch_waits < 1 || jalr_waits < 1 ||
                            stores != 1 || memory[2] !== 31)
                            $fatal(1, "Missing bypass coverage rs1=%0d rs2=%0d branch=%0d jalr=%0d stores=%0d",
                                wb_rs1_waits, wb_rs2_waits, branch_waits, jalr_waits, stores);
                        disable wait_for_result;
                    end
                end
                $fatal(1, "Bypass test timeout at %h", imem_addr);
            end
            rst_n = 0;
            #20;
        end
        $display("PASS: WB operands survive stalled MEM, MEM/WB priority, simultaneous sources, x0, branch/JALR recovery and hot reset");
        $finish;
    end
endmodule
