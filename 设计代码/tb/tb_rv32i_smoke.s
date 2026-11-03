`timescale 1ns/1ps

module tb_rv32i_smoke;
    reg clk = 1'b0;
    reg rst_n = 1'b0;
    wire [31:0] counter;
    wire [31:0] alu_result;

    rv32i_smoke dut (
        .clk(clk),
        .rst_n(rst_n),
        .counter(counter),
        .alu_result(alu_result)
    );

    always #5 clk = ~clk;

    initial begin
        $dumpfile("build/rv32i_smoke.vcd");
        $dumpvars(0, tb_rv32i_smoke);
        #12 rst_n = 1'b1;
        repeat (4) @(posedge clk);
        #1;
        if (counter !== 32'd16) begin
            $display("FAIL: counter=%0d, expected=16", counter);
            $fatal(1);
        end
        $display("PASS: counter=%0d, alu_result=%0d", counter, alu_result);
        $finish;
    end
endmodule
