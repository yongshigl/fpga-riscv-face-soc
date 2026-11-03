`timescale 1ns/1ps

module tb_rv32i_core;
    reg clk = 1'b0;
    reg rst_n = 1'b0;
    wire imem_valid;
    wire [31:0] imem_addr;
    reg [31:0] imem_rdata;
    wire dmem_valid;
    wire dmem_we;
    wire [31:0] dmem_addr;
    wire [31:0] dmem_wdata;
    wire [3:0] dmem_wstrb;
    reg [31:0] dmem_rdata;
    wire [31:0] current_pc;
    wire [31:0] debug_x10;
    wire halted;
    wire trap;
    reg [31:0] instruction_mem [0:255];
    reg [31:0] data_mem [0:255];
    integer index;

    function [31:0] swap_bytes;
        input [31:0] value;
        begin
            swap_bytes = {value[7:0], value[15:8], value[23:16], value[31:24]};
        end
    endfunction

    rv32i_core dut (
        .clk(clk), .rst_n(rst_n),
        .imem_valid(imem_valid), .imem_addr(imem_addr), .imem_rdata(imem_rdata),
        .dmem_valid(dmem_valid), .dmem_we(dmem_we), .dmem_addr(dmem_addr),
        .dmem_wdata(dmem_wdata), .dmem_wstrb(dmem_wstrb), .dmem_rdata(dmem_rdata),
        .current_pc(current_pc), .debug_x10(debug_x10), .halted(halted), .trap(trap)
    );

    always #5 clk = ~clk;

    always @* begin
        if (imem_addr[31:10] == 0)
            imem_rdata = swap_bytes(instruction_mem[imem_addr[9:2]]);
        else
            imem_rdata = 32'h00000013;
        if (dmem_addr[31:10] == 0)
            dmem_rdata = data_mem[dmem_addr[9:2]];
        else
            dmem_rdata = 32'd0;
    end

    always @(posedge clk) begin
        if (dmem_valid && dmem_we && dmem_addr[31:10] == 0) begin
            if (dmem_wstrb[0]) data_mem[dmem_addr[9:2]][7:0] <= dmem_wdata[7:0];
            if (dmem_wstrb[1]) data_mem[dmem_addr[9:2]][15:8] <= dmem_wdata[15:8];
            if (dmem_wstrb[2]) data_mem[dmem_addr[9:2]][23:16] <= dmem_wdata[23:16];
            if (dmem_wstrb[3]) data_mem[dmem_addr[9:2]][31:24] <= dmem_wdata[31:24];
        end
        if (dmem_valid && dmem_we && dmem_addr == 32'h00000100) begin
`ifdef COMPLIANCE
            if (dmem_wdata !== 32'h00001234) begin
                $display("FAIL: result=%h, expected=00001234", dmem_wdata);
`else
            if (dmem_wdata !== 32'd12) begin
                $display("FAIL: result=%0d, expected=12", dmem_wdata);
`endif
                $fatal(1);
            end
            $display("PASS: RV32I core result=%h pc=%h", dmem_wdata, current_pc);
            $finish;
        end
    end

    initial begin
`ifdef COMPLIANCE
        $readmemh("build/rv32i_compliance.hex", instruction_mem);
`else
        $readmemh("build/rv32i_test.hex", instruction_mem);
`endif
        for (index = 0; index < 256; index = index + 1)
            data_mem[index] = 32'd0;
        $dumpfile("build/rv32i_core.vcd");
        $dumpvars(0, tb_rv32i_core);
        #12 rst_n = 1'b1;
        repeat (100) @(posedge clk);
        $display("FAIL: timeout pc=%h halted=%b trap=%b x10=%0d", current_pc, halted, trap, debug_x10);
        $fatal(1);
    end
endmodule
