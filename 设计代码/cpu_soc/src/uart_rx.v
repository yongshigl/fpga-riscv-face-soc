`timescale 1ns/1ps

module uart_rx #(
    parameter integer CLOCK_HZ = 90000000,
    parameter integer BAUD_RATE = 921600
) (
    input wire clk, rst_n, rx,
    output reg [7:0] data,
    output reg valid,
    output reg framing_error
);
    localparam integer DIVIDER = (CLOCK_HZ + BAUD_RATE/2) / BAUD_RATE;
    localparam integer COUNT_BITS = $clog2(DIVIDER + 1);
    localparam IDLE = 0, START = 1, BITS = 2, STOP = 3, MARK = 4;
    (* ASYNC_REG = "TRUE" *) reg [2:0] synchronizer;
    reg [2:0] state, bit_number;
    reg [COUNT_BITS-1:0] count;
    reg [7:0] shift;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) synchronizer <= 3'b111;
        else synchronizer <= {synchronizer[1:0], rx};
    end
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= IDLE; bit_number <= 0; count <= 0;
            shift <= 0; data <= 0; valid <= 0; framing_error <= 0;
        end else begin
            valid <= 0; framing_error <= 0;
            case (state)
                IDLE: if (!synchronizer[2]) begin count <= DIVIDER/2-1; state <= START; end
                START: if (count != 0) count <= count - 1'b1;
                       else if (synchronizer[2]) state <= IDLE;
                       else begin state <= BITS; count <= DIVIDER-1; bit_number <= 0; end
                BITS: if (count != 0) count <= count - 1'b1;
                      else begin
                          shift[bit_number] <= synchronizer[2]; count <= DIVIDER-1;
                          if (bit_number == 7) state <= STOP;
                          else bit_number <= bit_number + 1'b1;
                      end
                STOP: if (count != 0) count <= count - 1'b1;
                      else if (synchronizer[2]) begin data <= shift; valid <= 1; state <= IDLE; end
                      else begin framing_error <= 1; count <= DIVIDER-1; state <= MARK; end
                MARK: if (!synchronizer[2]) count <= DIVIDER-1;
                      else if (count != 0) count <= count - 1'b1;
                      else state <= IDLE;
                default: state <= IDLE;
            endcase
        end
    end
endmodule
