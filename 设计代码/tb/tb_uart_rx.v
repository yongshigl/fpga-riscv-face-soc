`timescale 1ns/1ps

module tb_uart_rx;
    reg clk = 0, rst_n = 0, rx = 1;
    always #5 clk = ~clk;
    wire [7:0] data;
    wire valid, framing_error;
    uart_rx #(.CLOCK_HZ(100000), .BAUD_RATE(1000)) dut (
        .clk(clk), .rst_n(rst_n), .rx(rx), .data(data), .valid(valid), .framing_error(framing_error)
    );
    reg [7:0] expected [0:127];
    integer received = 0, errors = 0, operation, bit_number, period;
    always @(posedge clk) if (rst_n) begin
        if (framing_error) errors = errors + 1;
        if (valid) begin
            if (data !== expected[received]) $fatal(1, "UART byte %0d mismatch %h/%h", received, data, expected[received]);
            received = received + 1;
        end
    end
    task send;
        input [7:0] value;
        input integer bit_period;
        input good_stop;
        begin
            rx = 0; #(bit_period);
            for (bit_number = 0; bit_number < 8; bit_number = bit_number + 1) begin
                rx = value[bit_number]; #(bit_period);
            end
            rx = good_stop; #(bit_period);
        end
    endtask
    initial begin
        #17 rst_n = 1;
        #130 rx = 0; #40 rx = 1;
        #1500;
        for (operation = 0; operation < 100; operation = operation + 1) begin
            expected[operation] = $urandom;
            period = 990 + $urandom_range(0, 20);
            #($urandom_range(0, 20));
            send(expected[operation], period, 1);
        end
        #1000;
        if (received != 100 || errors != 0) $fatal(1, "UART continuous reception failed");
        send(8'ha5, 1000, 0);
        #5000 rx = 1; #2000;
        expected[100] = 8'h37;
        send(expected[100], 1000, 1);
        #1000;
        if (received != 101 || errors != 1) $fatal(1, "UART framing error recovery failed");
        #2 rst_n = 0; #18 rst_n = 1;
        #1000 expected[101] = 8'hc8;
        send(expected[101], 1000, 1);
        #1000;
        if (received != 102) $fatal(1, "UART reset recovery failed");
        $display("PASS: UART RX continuous random bytes, baud mismatch, false start, invalid stop and reset");
        $finish;
    end
    initial begin #2000000; $fatal(1, "UART RX timed out"); end
endmodule
