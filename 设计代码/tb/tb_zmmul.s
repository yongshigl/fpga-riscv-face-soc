`timescale 1ns/1ps

module tb_zmmul;
    reg clk = 1'b0;
    reg rst_n = 1'b0;
    wire [31:0] imem_addr;
    wire [31:0] imem_rdata;
    wire dmem_valid;
    wire dmem_we;
    wire [31:0] dmem_addr;
    wire [31:0] dmem_wdata;
    wire [3:0] dmem_wstrb;
    wire [31:0] debug_x10;
    reg [31:0] instruction_mem [0:255];
    reg [31:0] data_mem [0:255];
    integer index;

    function [31:0] swap_bytes;
        input [31:0] value;
        begin
            swap_bytes = {value[7:0], value[15:8], value[23:16], value[31:24]};
        end
    endfunction

    assign imem_rdata = imem_addr[31:10] == 0 ?
                        swap_bytes(instruction_mem[imem_addr[9:2]]) : 32'h00000013;

    rv32i_pipeline_core dut (
        .clk(clk), .rst_n(rst_n), .imem_valid(), .imem_addr(imem_addr),
        .imem_rdata(imem_rdata), .dmem_valid(dmem_valid), .dmem_we(dmem_we),
        .dmem_addr(dmem_addr), .dmem_wdata(dmem_wdata), .dmem_wstrb(dmem_wstrb),
        .dmem_rdata(dmem_addr[31:10] == 0 ? data_mem[dmem_addr[9:2]] : 32'd0),
        .irq_timer(1'b0), .irq_external(1'b0),
        .current_pc(), .debug_x10(debug_x10), .trap_taken(),
        .cycle_count(), .instret_count()
    );

    always #5 clk = ~clk;

    always @(posedge clk) begin
        if (dmem_valid && dmem_we && dmem_addr[31:10] == 0)
            data_mem[dmem_addr[9:2]] <= dmem_wdata;
    end

    initial begin
        for (index = 0; index < 256; index = index + 1) begin
            instruction_mem[index] = 32'h13000000;
            data_mem[index] = 32'd0;
        end
        $readmemh("build/mul/zmmul_test.hex", instruction_mem);
        #12 rst_n = 1'b1;
        repeat (100) @(negedge clk);
        if (debug_x10 !== 32'h1234 ||
            data_mem[64] !== 32'hfffffffe ||
            data_mem[65] !== 32'hffffffff ||
            data_mem[66] !== 32'hffffffff ||
            data_mem[67] !== 32'h00000001 ||
            data_mem[68] !== 32'h00000000 ||
            data_mem[69] !== 32'hffffffff ||
            data_mem[70] !== 32'h00000001 ||
            data_mem[71] !== 32'hffffffff ||
            data_mem[72] !== 32'h00000006 ||
            data_mem[73] !== 32'hffffffff ||
            data_mem[74] !== 32'h00000007 ||
            data_mem[75] !== 32'h0000000e ||
            data_mem[76] !== 32'h00000000)
            $fatal(1, "Zmmul results: %h %h %h %h %h %h %h %h %h %h %h %h %h",
                   data_mem[64], data_mem[65], data_mem[66], data_mem[67],
                   data_mem[68], data_mem[69], data_mem[70], data_mem[71],
                   data_mem[72], data_mem[73], data_mem[74], data_mem[75],
                   data_mem[76]);
        $display("PASS: Zmmul edge cases");
        $finish;
    end
endmodule
