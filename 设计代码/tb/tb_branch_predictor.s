`timescale 1ns/1ps

module tb_branch_predictor;
    reg clk = 1'b0;
    reg rst_n = 1'b0;
    wire [31:0] imem_addr;
    wire [31:0] imem_rdata;
    wire [31:0] debug_x10;
    wire [63:0] cycle_count;
    reg [31:0] instruction_mem [0:255];
    integer index;
    integer mispredicts = 0;
    integer predicted_fetches = 0;

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
        .imem_rdata(imem_rdata), .dmem_valid(), .dmem_we(), .dmem_addr(),
        .dmem_wdata(), .dmem_wstrb(), .dmem_rdata(32'd0),
        .irq_timer(1'b0), .irq_external(1'b0), .current_pc(),
        .debug_x10(debug_x10), .trap_taken(), .cycle_count(cycle_count),
        .instret_count()
    );

    always #5 clk = ~clk;

    always @(posedge clk) begin
        if (rst_n && dut.ex_mispredict)
            mispredicts <= mispredicts + 1;
        if (rst_n && dut.fetch_btb_hit && dut.btb_counter[dut.fetch_btb_index][1])
            predicted_fetches <= predicted_fetches + 1;
    end

    initial begin
        for (index = 0; index < 256; index = index + 1)
            instruction_mem[index] = 32'h13000000;
        $readmemh("build/branch_predictor_test.hex", instruction_mem);
        #12 rst_n = 1'b1;
        repeat (110) @(negedge clk);
        if (debug_x10 !== 32'd20 || mispredicts != 3 || predicted_fetches < 20) begin
            $display("FAIL: BTB result=%0d misses=%0d predicted=%0d cycles=%0d",
                     debug_x10, mispredicts, predicted_fetches, cycle_count);
            $fatal(1);
        end
        $display("PASS: BTB result=%0d misses=%0d predicted=%0d cycles=%0d",
                 debug_x10, mispredicts, predicted_fetches, cycle_count);
        $finish;
    end
endmodule
