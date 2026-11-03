`timescale 1ns/1ps

module camera_packet_tx (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        start,
    input  wire [7:0]  packet_type,
    input  wire [7:0]  camera,
    input  wire [7:0]  format,
    input  wire [7:0]  flags,
    input  wire [31:0] sequence_number,
    input  wire [31:0] payload_length,
    input  wire [7:0]  payload_data,
    output reg  [31:0] payload_index,
    output wire       busy,
    output reg        done,
    output reg  [7:0]  tx_data,
    output wire       tx_valid,
    input  wire       tx_ready
);
    localparam [1:0] IDLE = 0, HEADER = 1, PAYLOAD = 2, CHECKSUM = 3;
    reg [1:0] state;
    reg [3:0] header_index;
    reg [1:0] checksum_index;
    reg [31:0] crc;
    reg [7:0] type_latched, camera_latched, format_latched, flags_latched;
    reg [31:0] sequence_latched, length_latched;
    wire accepted = tx_valid && tx_ready;

    assign busy = state != IDLE;
    assign tx_valid = state != IDLE;

    function [31:0] crc32_byte;
        input [31:0] previous;
        input [7:0] value;
        reg [31:0] work;
        integer bit_number;
        begin
            work = previous ^ {24'd0, value};
            for (bit_number = 0; bit_number < 8; bit_number = bit_number + 1)
                work = work[0] ? (work >> 1) ^ 32'hedb88320 : work >> 1;
            crc32_byte = work;
        end
    endfunction

    always @* begin
        tx_data = 0;
        case (state)
            HEADER: begin
                case (header_index)
                    0: tx_data = "P";
                    1: tx_data = "G";
                    2: tx_data = "C";
                    3: tx_data = "2";
                    4: tx_data = type_latched;
                    5: tx_data = camera_latched;
                    6: tx_data = format_latched;
                    7: tx_data = flags_latched;
                    8: tx_data = sequence_latched[7:0];
                    9: tx_data = sequence_latched[15:8];
                    10: tx_data = sequence_latched[23:16];
                    11: tx_data = sequence_latched[31:24];
                    12: tx_data = length_latched[7:0];
                    13: tx_data = length_latched[15:8];
                    14: tx_data = length_latched[23:16];
                    15: tx_data = length_latched[31:24];
                endcase
            end
            PAYLOAD: tx_data = payload_data;
            CHECKSUM: tx_data = (~crc) >> (checksum_index * 8);
        endcase
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= IDLE;
            header_index <= 0;
            checksum_index <= 0;
            payload_index <= 0;
            done <= 0;
            crc <= 32'hffffffff;
            type_latched <= 0;
            camera_latched <= 0;
            format_latched <= 0;
            flags_latched <= 0;
            sequence_latched <= 0;
            length_latched <= 0;
        end else begin
            done <= 0;
            case (state)
                IDLE: if (start) begin
                    type_latched <= packet_type;
                    camera_latched <= camera;
                    format_latched <= format;
                    flags_latched <= flags;
                    sequence_latched <= sequence_number;
                    length_latched <= payload_length;
                    header_index <= 0;
                    payload_index <= 0;
                    checksum_index <= 0;
                    crc <= 32'hffffffff;
                    state <= HEADER;
                end
                HEADER: if (accepted) begin
                    crc <= crc32_byte(crc, tx_data);
                    if (header_index == 15) state <= length_latched == 0 ? CHECKSUM : PAYLOAD;
                    else header_index <= header_index + 1'b1;
                end
                PAYLOAD: if (accepted) begin
                    crc <= crc32_byte(crc, tx_data);
                    if (payload_index == length_latched-1) state <= CHECKSUM;
                    else payload_index <= payload_index + 1'b1;
                end
                CHECKSUM: if (accepted) begin
                    if (checksum_index == 3) begin
                        state <= IDLE;
                        done <= 1;
                    end else checksum_index <= checksum_index + 1'b1;
                end
            endcase
        end
    end
endmodule
