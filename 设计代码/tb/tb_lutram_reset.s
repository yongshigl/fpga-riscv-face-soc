`timescale 1ns/1ps

module tb_lutram_reset;
    reg clk = 0;
    reg rst_n = 0;
    reg [31:0] instruction_mem [0:15];
    wire [31:0] imem_addr, dmem_addr, dmem_wdata, debug_x10;
    wire dmem_valid, dmem_we;
    integer phase = 0;
    integer stores = 0;
    integer i;

    rv32i_pipeline_core dut (
        .clk(clk), .rst_n(rst_n), .imem_addr(imem_addr),
        .imem_rdata(instruction_mem[imem_addr[5:2]]), .dmem_valid(dmem_valid),
        .dmem_we(dmem_we), .dmem_addr(dmem_addr), .dmem_wdata(dmem_wdata),
        .dmem_rdata(32'd0), .irq_timer(1'b0), .irq_external(1'b0),
        .debug_x10(debug_x10)
    );
    always #5 clk = ~clk;

    always @(posedge clk) begin
        if (rst_n && dmem_valid && dmem_we) begin
            if (phase == 0) begin
                if (dmem_addr !== 0 || dmem_wdata !== 32'h123) $fatal(1, "Cold boot failed");
                stores = 1;
            end else begin
                case (stores)
                    0: if (dmem_addr !== 4 || dmem_wdata !== 0) $fatal(1, "Stale register survived reset");
                    1: if (dmem_addr !== 8 || dmem_wdata !== 0) $fatal(1, "Stale operand survived reset");
                    2: begin
                        if (dmem_addr !== 12 || dmem_wdata !== 32'h456) $fatal(1, "Write after reset failed");
                        $display("PASS: LUT RAM cold boot, trained BTB invalidation, warm reset and both operand reads");
                        $finish;
                    end
                    default: $fatal(1, "Unexpected store");
                endcase
                stores = stores + 1;
            end
        end
    end

    initial begin
        for (i = 0; i < 16; i = i + 1) instruction_mem[i] = 32'h00000013;
        instruction_mem[0] = 32'h12300513; // addi a0, zero, 0x123
        instruction_mem[1] = 32'h02a00393; // addi t2, zero, 42
        instruction_mem[2] = 32'h00700293; // addi t0, zero, 7
        instruction_mem[3] = 32'hfff28293; // addi t0, t0, -1
        instruction_mem[4] = 32'hfe029ee3; // bne t0, zero, -4
        instruction_mem[5] = 32'h00a02023; // sw a0, 0(zero)
        instruction_mem[6] = 32'h0000006f;
        #12 rst_n = 1;
        wait(stores == 1);
        @(negedge clk);
        rst_n = 0;
        phase = 1;
        stores = 0;
        instruction_mem[0] = 32'h00a02223; // sw a0, 4(zero)
        instruction_mem[1] = 32'h00750333; // add t1, a0, t2
        instruction_mem[2] = 32'h00602423; // sw t1, 8(zero)
        instruction_mem[3] = 32'h45600513;
        instruction_mem[4] = 32'h00a02623;
        instruction_mem[5] = 32'h0000006f;
        #1;
        if (debug_x10 !== 0 || dut.regs_valid !== 0) $fatal(1, "Reset validity failed");
        for (i = 0; i < dut.BTB_ENTRIES; i = i + 1)
            if (dut.btb_valid[i] !== 0) $fatal(1, "Trained prediction survived reset");
        #11 rst_n = 1;
    end
    initial begin
        #20000;
        $fatal(1, "Warm reset timeout");
    end
endmodule
