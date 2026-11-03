`timescale 1ns/1ps

module sccb_master #(
    parameter integer CLOCK_HZ = 27_000_000,
    parameter integer BUS_HZ = 100_000
) (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        start,
    input  wire        read,
    input  wire [15:0] address,
    input  wire [7:0]  write_data,
    output reg  [7:0]  read_data,
    output wire       busy,
    output reg        done,
    output reg        nack,
    output reg        scl,
    inout  wire       sda
);
    localparam integer QUARTER = CLOCK_HZ / (BUS_HZ * 4);
    localparam [4:0] IDLE = 0, START_FREE = 1, START_HOLD = 2,
        TX_LOW = 3, TX_HIGH = 4, ACK_LOW = 5, ACK_HIGH = 6,
        RESTART_LOW = 7, RESTART_HIGH = 8, RESTART_HOLD = 9,
        RX_LOW = 10, RX_HIGH = 11, NACK_LOW = 12, NACK_HIGH = 13,
        STOP_LOW = 14, STOP_HIGH = 15, STOP_FREE = 16;

    reg [4:0] state;
    reg [15:0] count;
    reg drive_low;
    reg [1:0] sda_sync;
    reg read_latched;
    reg [15:0] address_latched;
    reg [7:0] data_latched;
    reg [7:0] tx_byte;
    reg [2:0] bit_index;
    reg [2:0] byte_index;
    reg [7:0] rx_byte;

    assign sda = drive_low ? 1'b0 : 1'bz;
    assign busy = state != IDLE;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) sda_sync <= 2'b11;
        else sda_sync <= {sda_sync[0], sda};
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= IDLE;
            count <= 0;
            drive_low <= 0;
            scl <= 1;
            done <= 0;
            nack <= 0;
            read_data <= 0;
            read_latched <= 0;
            address_latched <= 0;
            data_latched <= 0;
            tx_byte <= 0;
            bit_index <= 7;
            byte_index <= 0;
            rx_byte <= 0;
        end else begin
            done <= 0;
            count <= count + 1'b1;
            case (state)
                IDLE: begin
                    count <= 0;
                    scl <= 1;
                    drive_low <= 0;
                    if (start) begin
                        read_latched <= read;
                        address_latched <= address;
                        data_latched <= write_data;
                        tx_byte <= 8'h78;
                        bit_index <= 7;
                        byte_index <= 0;
                        nack <= 0;
                        rx_byte <= 0;
                        state <= START_FREE;
                    end
                end
                START_FREE: if (count == QUARTER*2-1) begin
                    drive_low <= 1;
                    count <= 0;
                    state <= START_HOLD;
                end
                START_HOLD, RESTART_HOLD: if (count == QUARTER-1) begin
                    scl <= 0;
                    drive_low <= !tx_byte[bit_index];
                    count <= 0;
                    state <= TX_LOW;
                end
                TX_LOW: if (count == QUARTER*2-1) begin
                    scl <= 1;
                    count <= 0;
                    state <= TX_HIGH;
                end
                TX_HIGH: if (count == QUARTER*2-1) begin
                    scl <= 0;
                    count <= 0;
                    if (bit_index == 0) begin
                        drive_low <= 0;
                        state <= ACK_LOW;
                    end else begin
                        bit_index <= bit_index - 1'b1;
                        drive_low <= !tx_byte[bit_index-1'b1];
                        state <= TX_LOW;
                    end
                end
                ACK_LOW: if (count == QUARTER*2-1) begin
                    scl <= 1;
                    count <= 0;
                    state <= ACK_HIGH;
                end
                ACK_HIGH: if (count == QUARTER*2-1) begin
                    scl <= 0;
                    count <= 0;
                    bit_index <= 7;
                    if (sda_sync[1]) begin
                        nack <= 1;
                        drive_low <= 1;
                        state <= STOP_LOW;
                    end else if (byte_index == 0) begin
                        byte_index <= 1;
                        tx_byte <= address_latched[15:8];
                        drive_low <= !address_latched[15];
                        state <= TX_LOW;
                    end else if (byte_index == 1) begin
                        byte_index <= 2;
                        tx_byte <= address_latched[7:0];
                        drive_low <= !address_latched[7];
                        state <= TX_LOW;
                    end else if (byte_index == 2 && read_latched) begin
                        drive_low <= 0;
                        state <= RESTART_LOW;
                    end else if (byte_index == 2) begin
                        byte_index <= 3;
                        tx_byte <= data_latched;
                        drive_low <= !data_latched[7];
                        state <= TX_LOW;
                    end else if (read_latched) begin
                        drive_low <= 0;
                        state <= RX_LOW;
                    end else begin
                        drive_low <= 1;
                        state <= STOP_LOW;
                    end
                end
                RESTART_LOW: if (count == QUARTER*2-1) begin
                    scl <= 1;
                    count <= 0;
                    state <= RESTART_HIGH;
                end
                RESTART_HIGH: if (count == QUARTER*2-1) begin
                    drive_low <= 1;
                    tx_byte <= 8'h79;
                    byte_index <= 3;
                    count <= 0;
                    state <= RESTART_HOLD;
                end
                RX_LOW: if (count == QUARTER*2-1) begin
                    scl <= 1;
                    count <= 0;
                    state <= RX_HIGH;
                end
                RX_HIGH: if (count == QUARTER*2-1) begin
                    rx_byte[bit_index] <= sda_sync[1];
                    scl <= 0;
                    count <= 0;
                    if (bit_index == 0) begin
                        read_data <= {rx_byte[7:1], sda_sync[1]};
                        state <= NACK_LOW;
                    end else begin
                        bit_index <= bit_index - 1'b1;
                        state <= RX_LOW;
                    end
                end
                NACK_LOW: if (count == QUARTER*2-1) begin
                    scl <= 1;
                    count <= 0;
                    state <= NACK_HIGH;
                end
                NACK_HIGH: if (count == QUARTER*2-1) begin
                    scl <= 0;
                    drive_low <= 1;
                    count <= 0;
                    state <= STOP_LOW;
                end
                STOP_LOW: if (count == QUARTER*2-1) begin
                    scl <= 1;
                    count <= 0;
                    state <= STOP_HIGH;
                end
                STOP_HIGH: if (count == QUARTER*2-1) begin
                    drive_low <= 0;
                    count <= 0;
                    state <= STOP_FREE;
                end
                STOP_FREE: if (count == QUARTER*2-1) begin
                    done <= 1;
                    count <= 0;
                    state <= IDLE;
                end
                default: state <= IDLE;
            endcase
        end
    end
endmodule
