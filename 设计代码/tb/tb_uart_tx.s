`timescale 1ns/1ps

module tb_uart_tx;
    localparam integer CLKS_PER_BIT = 4;
    reg clk = 1'b0;
    reg rst_n = 1'b0;
    reg [7:0] data = 8'd0;
    reg valid = 1'b0;
    wire ready;
    wire tx;
    wire busy;
    reg [7:0] received;
    integer bit_index;

    uart_tx #(.CLOCK_HZ(400), .BAUD_RATE(100)) dut (
        .clk(clk), .rst_n(rst_n), .data(data), .valid(valid),
        .ready(ready), .tx(tx), .busy(busy)
    );

    always #5 clk = ~clk;

    initial begin
        $dumpfile("build/uart_tx.vcd");
        $dumpvars(0, tb_uart_tx);
        #12 rst_n = 1'b1;
        @(posedge clk);
        data <= 8'h41;
        valid <= 1'b1;
        @(posedge clk);
        valid <= 1'b0;
        wait (tx == 1'b0);
        repeat (CLKS_PER_BIT + CLKS_PER_BIT / 2) @(posedge clk);
        for (bit_index = 0; bit_index < 8; bit_index = bit_index + 1) begin
            received[bit_index] = tx;
            repeat (CLKS_PER_BIT) @(posedge clk);
        end
        if (received !== 8'h41) begin
            $display("FAIL: UART received=%h expected=41", received);
            $fatal(1);
        end
        if (tx !== 1'b1) begin
            $display("FAIL: UART stop bit is not high");
            $fatal(1);
        end
        $display("PASS: UART transmitted byte=%h", received);
        $finish;
    end
endmodule
