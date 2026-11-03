`timescale 1ns/1ps

module tb_c_firmware;
    reg clk = 1'b0;
    reg rst_n = 1'b0;
    wire [31:0] gpio_out;
    wire halted;
    wire trap;
    wire [31:0] current_pc;

    rv32i_soc #(.INIT_FILE("build/firmware.hex")) dut (
        .clk(clk), .rst_n(rst_n), .gpio_out(gpio_out),
        .uart_tx_data(), .uart_tx_strobe(),
        .halted(halted), .trap(trap), .current_pc(current_pc)
    );

    always #5 clk = ~clk;

    initial begin
        $dumpfile("build/c_firmware.vcd");
        $dumpvars(0, tb_c_firmware);
        #12 rst_n = 1'b1;
        wait (halted);
        #1;
        if (gpio_out !== 32'h00005637 || trap) begin
            $display("FAIL: C firmware gpio=%h pc=%h trap=%b", gpio_out, current_pc, trap);
            $fatal(1);
        end
        $display("PASS: C firmware gpio=%h pc=%h", gpio_out, current_pc);
        $finish;
    end

    initial begin
        #20000;
        $display("FAIL: C firmware timeout pc=%h", current_pc);
        $fatal(1);
    end
endmodule
