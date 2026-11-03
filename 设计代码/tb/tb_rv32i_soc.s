`timescale 1ns/1ps

module tb_rv32i_soc;
    reg clk = 1'b0;
    reg rst_n = 1'b0;
    wire [31:0] gpio_out;
    wire [7:0] uart_tx_data;
    wire uart_tx_strobe;
    wire halted;
    wire trap;
    wire [31:0] current_pc;

    rv32i_soc #(.INIT_FILE("build/rv32i_compliance.hex")) dut (
        .clk(clk), .rst_n(rst_n), .gpio_out(gpio_out),
        .uart_tx_data(uart_tx_data), .uart_tx_strobe(uart_tx_strobe),
        .halted(halted), .trap(trap), .current_pc(current_pc)
    );

    always #5 clk = ~clk;

    initial begin
        $dumpfile("build/rv32i_soc.vcd");
        $dumpvars(0, tb_rv32i_soc);
        #12 rst_n = 1'b1;
        wait (halted);
        #1;
        if (gpio_out !== 32'h00001234) begin
            $display("FAIL: soc gpio=%h pc=%h trap=%b", gpio_out, current_pc, trap);
            $fatal(1);
        end
        $display("PASS: RV32I SoC gpio=%h pc=%h trap=%b", gpio_out, current_pc, trap);
        $finish;
    end

    initial begin
        #10000;
        $display("FAIL: SoC timeout pc=%h halted=%b gpio=%h", current_pc, halted, gpio_out);
        $fatal(1);
    end
endmodule
