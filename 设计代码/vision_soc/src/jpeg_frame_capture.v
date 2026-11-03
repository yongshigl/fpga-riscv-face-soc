`timescale 1ns/1ps

module jpeg_frame_capture #(
    parameter integer BUFFER_BYTES = 65536,
    parameter integer ADDR_BITS = $clog2(BUFFER_BYTES)
) (
    input wire pixel_clk, sys_clk, rst_n, configured,
    input wire vsync, href,
    input wire [7:0] data,
    input wire rearm_toggle,
    output reg rearm_ack,
    input wire [ADDR_BITS-1:0] read_addr,
    output reg [7:0] read_data,
    output reg frame_done,
    output reg [31:0] frame_sequence,
    output reg [ADDR_BITS:0] frame_bytes,
    output reg [31:0] overflow_frames, incomplete_frames
);
    localparam WAIT_RISE = 0, WAIT_FALL = 1, SEEK_SOI = 2, CAPTURE = 3, HOLD = 4;
    reg [2:0] state;
    (* ASYNC_REG = "TRUE" *) reg [1:0] pixel_reset, rearm_sync, configured_sync;
    wire pixel_rst_n = pixel_reset[1];
    reg vsync_last, previous_ff;
    reg [31:0] source_sequence;
    reg [ADDR_BITS:0] byte_count;
    reg [7:0] memory [0:BUFFER_BYTES-1];
    localparam MARKER_PREFIX = 0, MARKER_CODE = 1, LENGTH_HIGH = 2,
        LENGTH_LOW = 3, SKIP_HEADER = 4, ENTROPY = 5;
    reg [2:0] jpeg_state;
    reg [7:0] segment_high;
    reg [15:0] segment_remaining;
    reg scan_header;
    wire [15:0] segment_length = {segment_high, data};
    wire seek_ff = state == SEEK_SOI && data == 8'hff;
    wire seek_soi = state == SEEK_SOI && previous_ff && data == 8'hd8;
    wire payload_byte = state == CAPTURE && byte_count < BUFFER_BYTES;
    wire write_enable = pixel_rst_n && configured_sync[1] && rearm_sync[1] == rearm_ack &&
        href && !vsync && (seek_ff || seek_soi || payload_byte);
    wire [ADDR_BITS-1:0] write_address = seek_ff ? 0 : seek_soi ? 1 : byte_count[ADDR_BITS-1:0];

    always @(posedge pixel_clk) if (write_enable) memory[write_address] <= data;
    always @(posedge sys_clk) read_data <= memory[read_addr];

    always @(posedge pixel_clk or negedge rst_n) begin
        if (!rst_n) pixel_reset <= 0;
        else pixel_reset <= {pixel_reset[0], 1'b1};
    end

    // Completed buffers and their metadata remain immutable until rearm is acknowledged.
    always @(posedge pixel_clk or negedge pixel_rst_n) begin
        if (!pixel_rst_n) begin
            state <= WAIT_RISE;
            rearm_sync <= 0; configured_sync <= 0; rearm_ack <= 0;
            vsync_last <= 0; previous_ff <= 0;
            source_sequence <= 0; byte_count <= 0;
            frame_done <= 0; frame_sequence <= 0; frame_bytes <= 0;
            overflow_frames <= 0; incomplete_frames <= 0;
            jpeg_state <= MARKER_PREFIX; segment_high <= 0;
            segment_remaining <= 0; scan_header <= 0;
        end else begin
            rearm_sync <= {rearm_sync[0], rearm_toggle};
            configured_sync <= {configured_sync[0], configured};
            vsync_last <= vsync;
            if (vsync && !vsync_last) source_sequence <= source_sequence + 1'b1;
            if (rearm_sync[1] != rearm_ack) begin
                rearm_ack <= rearm_sync[1];
                state <= WAIT_RISE; frame_done <= 0; byte_count <= 0; previous_ff <= 0;
            end else if (!configured_sync[1]) begin
                state <= WAIT_RISE; frame_done <= 0; byte_count <= 0; previous_ff <= 0;
            end else case (state)
                WAIT_RISE: if (vsync && !vsync_last) state <= WAIT_FALL;
                WAIT_FALL: if (!vsync && vsync_last) begin
                    state <= SEEK_SOI; byte_count <= 0; previous_ff <= 0;
                end
                SEEK_SOI: begin
                    if (vsync && !vsync_last) begin
                        incomplete_frames <= incomplete_frames + 1'b1;
                        previous_ff <= 0; state <= WAIT_FALL;
                    end else if (href && !vsync) begin
                        previous_ff <= data == 8'hff;
                        if (previous_ff && data == 8'hd8) begin
                            byte_count <= 2; state <= CAPTURE;
                            jpeg_state <= MARKER_PREFIX;
                        end
                    end
                end
                CAPTURE: begin
                    if (vsync && !vsync_last) begin
                        incomplete_frames <= incomplete_frames + 1'b1;
                        previous_ff <= 0; byte_count <= 0; state <= WAIT_FALL;
                    end else if (href && !vsync) begin
                        if (byte_count == BUFFER_BYTES) begin
                            overflow_frames <= overflow_frames + 1'b1;
                            previous_ff <= 0; byte_count <= 0; state <= WAIT_RISE;
                        end else begin
                            byte_count <= byte_count + 1'b1;
                            previous_ff <= data == 8'hff;
                            // Length-delimited JPEG headers may contain marker-like bytes.
                            // Only recognize EOI after the SOS header has ended.
                            case (jpeg_state)
                                MARKER_PREFIX: if (data == 8'hff) jpeg_state <= MARKER_CODE;
                                MARKER_CODE: if (data != 8'hff) begin
                                    scan_header <= data == 8'hda;
                                    jpeg_state <= LENGTH_HIGH;
                                end
                                LENGTH_HIGH: begin segment_high <= data; jpeg_state <= LENGTH_LOW; end
                                LENGTH_LOW: begin
                                    if (segment_length < 2) begin
                                        incomplete_frames <= incomplete_frames + 1'b1;
                                        byte_count <= 0; state <= WAIT_RISE;
                                    end else if (segment_length == 2) begin
                                        jpeg_state <= scan_header ? ENTROPY : MARKER_PREFIX;
                                        previous_ff <= 0;
                                    end else begin
                                        segment_remaining <= segment_length - 2;
                                        jpeg_state <= SKIP_HEADER;
                                    end
                                end
                                SKIP_HEADER: begin
                                    segment_remaining <= segment_remaining - 1'b1;
                                    if (segment_remaining == 1) begin
                                        jpeg_state <= scan_header ? ENTROPY : MARKER_PREFIX;
                                        previous_ff <= 0;
                                    end
                                end
                                default: begin end
                            endcase
                            if (jpeg_state == ENTROPY && previous_ff && data == 8'hd9) begin
                                frame_bytes <= byte_count + 1'b1;
                                frame_sequence <= source_sequence;
                                frame_done <= 1; state <= HOLD;
                            end
                        end
                    end
                end
                HOLD: begin end
                default: state <= WAIT_RISE;
            endcase
        end
    end
endmodule
