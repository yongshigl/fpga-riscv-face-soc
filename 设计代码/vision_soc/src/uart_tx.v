`timescale 1ns/1ps

module uart_tx #(
    parameter integer CLOCK_HZ = 50_000_000,
    parameter integer BAUD_RATE = 115_200,
    parameter integer CLKS_PER_BIT = CLOCK_HZ / BAUD_RATE
) (
    input  wire       clk,
    input  wire       rst_n,
    input  wire [7:0] data,
    input  wire       valid,
    output wire       ready,
    output reg        tx,
    output reg        busy
);

    reg [31:0] clock_count;
    reg [3:0] bit_index;
    reg [9:0] shift_reg;
    localparam integer TERMINAL_COUNT = CLKS_PER_BIT - 1;

    assign ready = !busy;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            clock_count <= 32'd0;
            bit_index <= 4'd0;
            shift_reg <= 10'h3ff;
            tx <= 1'b1;
            busy <= 1'b0;
        end else if (!busy) begin
            tx <= 1'b1;
            clock_count <= 32'd0;
            bit_index <= 4'd0;
            if (valid) begin
                shift_reg <= {1'b1, data, 1'b0};
                tx <= 1'b0;
                busy <= 1'b1;
            end
        end else if (clock_count == TERMINAL_COUNT) begin
            clock_count <= 32'd0;
            if (bit_index == 4'd9) begin
                tx <= 1'b1;
                busy <= 1'b0;
            end else begin
                bit_index <= bit_index + 4'd1;
                shift_reg <= {1'b1, shift_reg[9:1]};
                tx <= shift_reg[1];
            end
        end else begin
            clock_count <= clock_count + 32'd1;
        end
    end

endmodule
