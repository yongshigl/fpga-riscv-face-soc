`timescale 1ns/1ps

module frame_capture #(
    parameter integer SOURCE_WIDTH = 1280,
    parameter integer SOURCE_HEIGHT = 720,
    parameter integer DOWNSAMPLE_BITS = 4,
    parameter integer AVERAGE_2X2 = 0,
    parameter integer PIXEL_BYTES = 1,
    parameter integer PREVIEW_WIDTH = SOURCE_WIDTH >> DOWNSAMPLE_BITS,
    parameter integer PREVIEW_HEIGHT = SOURCE_HEIGHT >> DOWNSAMPLE_BITS,
    parameter integer FRAME_PIXELS = PREVIEW_WIDTH * PREVIEW_HEIGHT,
    parameter integer ADDR_WIDTH = $clog2(FRAME_PIXELS),
    parameter integer RAM_BANK_ADDR_BITS = 0
) (
    input  wire        pixel_clk,
    input  wire        sys_clk,
    input  wire        rst_n,
    input  wire        vsync,
    input  wire        href,
    input  wire        pixel_valid,
    input  wire [15:0] pixel,
    input  wire        rearm_toggle,
    output wire        rearm_ack,
    input  wire [ADDR_WIDTH-1:0] read_addr,
    output reg  [PIXEL_BYTES*8-1:0] read_data,
    output reg         frame_done,
    output reg  [31:0] frame_sequence,
    output wire [ADDR_WIDTH:0] samples
);
    reg [$clog2(SOURCE_WIDTH+2)-1:0] x;
    reg [$clog2(SOURCE_HEIGHT+2)-1:0] y;
    reg [ADDR_WIDTH:0] sample_count;
    reg vsync_last;
    reg href_last;
    reg capturing;
    reg line_error;
    reg [2:0] rearm_sync;

    wire sample_position = AVERAGE_2X2 ? x[0] && y[0] :
                        (x & ((1 << DOWNSAMPLE_BITS)-1)) == 0 &&
                        (y & ((1 << DOWNSAMPLE_BITS)-1)) == 0;
    wire sample_pixel = capturing && href && pixel_valid && x < SOURCE_WIDTH &&
                        y < SOURCE_HEIGHT && sample_position;
    wire [ADDR_WIDTH-1:0] write_addr =
        (y >> DOWNSAMPLE_BITS) * PREVIEW_WIDTH + (x >> DOWNSAMPLE_BITS);
    assign samples = sample_count;
    assign rearm_ack = rearm_sync[2];

    wire [15:0] filtered_pixel;
    generate if (AVERAGE_2X2) begin : average_filter
        reg [18:0] previous_line [0:PREVIEW_WIDTH-1];
        reg [18:0] line_sum;
        reg [15:0] first_pixel;
        wire [$clog2(PREVIEW_WIDTH)-1:0] column = x >> 1;
        wire [5:0] red_pair = {1'b0, first_pixel[15:11]} + {1'b0, pixel[15:11]};
        wire [6:0] green_pair = {1'b0, first_pixel[10:5]} + {1'b0, pixel[10:5]};
        wire [5:0] blue_pair = {1'b0, first_pixel[4:0]} + {1'b0, pixel[4:0]};
        wire [6:0] red_sum = {1'b0, line_sum[18:13]} + {1'b0, red_pair} + 7'd2;
        wire [7:0] green_sum = {1'b0, line_sum[12:6]} + {1'b0, green_pair} + 8'd2;
        wire [6:0] blue_sum = {1'b0, line_sum[5:0]} + {1'b0, blue_pair} + 7'd2;
        assign filtered_pixel = {red_sum[6:2], green_sum[7:2], blue_sum[6:2]};

        always @(posedge pixel_clk) begin
            if (x < SOURCE_WIDTH) line_sum <= previous_line[column];
            if (capturing && href && pixel_valid && x < SOURCE_WIDTH) begin
                if (!x[0]) first_pixel <= pixel;
                else if (!y[0]) previous_line[column] <= {red_pair, green_pair, blue_pair};
            end
        end
    end else begin : direct_filter
        assign filtered_pixel = pixel;
    end endgenerate

    wire write_enable = rst_n && sample_pixel && !frame_done &&
                        rearm_sync[2] == rearm_sync[1] && vsync_last == vsync;
    wire [PIXEL_BYTES*8-1:0] write_pixel = PIXEL_BYTES == 2 ? filtered_pixel :
                        {filtered_pixel[10:5], filtered_pixel[10:9]};
    generate if (RAM_BANK_ADDR_BITS == 0) begin : flat_memory
        reg [PIXEL_BYTES*8-1:0] frame [0:FRAME_PIXELS-1];
        always @(posedge pixel_clk)
            if (write_enable) frame[write_addr] <= write_pixel;
        always @(posedge sys_clk)
            read_data <= frame[read_addr];
    end else begin : banked_memory
        localparam integer BANK_BITS = RAM_BANK_ADDR_BITS < ADDR_WIDTH ? RAM_BANK_ADDR_BITS : ADDR_WIDTH;
        localparam integer BANK_WORDS = 1 << BANK_BITS;
        localparam integer BANK_COUNT = (FRAME_PIXELS + BANK_WORDS - 1) / BANK_WORDS;
        localparam integer BANK_SELECT_BITS = BANK_COUNT > 1 ? $clog2(BANK_COUNT) : 1;
        wire [PIXEL_BYTES*8-1:0] bank_data [0:BANK_COUNT-1];
        reg [BANK_SELECT_BITS-1:0] read_bank;
        genvar bank_index;
        for (bank_index = 0; bank_index < BANK_COUNT; bank_index = bank_index + 1) begin : banks
            localparam integer WORDS = FRAME_PIXELS - bank_index * BANK_WORDS < BANK_WORDS ?
                                      FRAME_PIXELS - bank_index * BANK_WORDS : BANK_WORDS;
            reg [PIXEL_BYTES*8-1:0] frame [0:WORDS-1];
            reg [PIXEL_BYTES*8-1:0] output_pixel;
            always @(posedge pixel_clk)
                if (write_enable && (write_addr >> BANK_BITS) == bank_index)
                    frame[write_addr[BANK_BITS-1:0]] <= write_pixel;
            always @(posedge sys_clk)
                if ((read_addr >> BANK_BITS) == bank_index)
                    output_pixel <= frame[read_addr[BANK_BITS-1:0]];
            assign bank_data[bank_index] = output_pixel;
        end
        always @(posedge sys_clk) begin
            read_bank <= read_addr >> BANK_BITS;
            read_data <= bank_data[read_bank];
        end
    end endgenerate

    always @(posedge pixel_clk or negedge rst_n) begin
        if (!rst_n) begin
            x <= 0;
            y <= 0;
            sample_count <= 0;
            vsync_last <= 1'b1;
            href_last <= 1'b0;
            capturing <= 1'b0;
            frame_done <= 1'b0;
            frame_sequence <= 0;
            line_error <= 0;
            rearm_sync <= 0;
        end else begin
            rearm_sync <= {rearm_sync[1:0], rearm_toggle};
            vsync_last <= vsync;
            href_last <= href;
            if (rearm_sync[2] != rearm_sync[1]) begin
                capturing <= 1'b0;
                frame_done <= 1'b0;
                sample_count <= 0;
                x <= 0;
                y <= 0;
                line_error <= 0;
            end else if (!frame_done) begin
                if (vsync_last && !vsync) begin
                    x <= 0;
                    y <= 0;
                    sample_count <= 0;
                    capturing <= 1'b1;
                    line_error <= 0;
                end else if (!vsync_last && vsync) begin
                    capturing <= 1'b0;
                    if (sample_count == FRAME_PIXELS && y == SOURCE_HEIGHT && !line_error) begin
                        frame_done <= 1'b1;
                        frame_sequence <= frame_sequence + 1'b1;
                    end
                end else if (capturing) begin
                    if (href_last && !href) begin
                        if (x != SOURCE_WIDTH) line_error <= 1;
                        x <= 0;
                        if (y < SOURCE_HEIGHT+1) y <= y + 1'b1;
                        else line_error <= 1;
                    end else if (pixel_valid && href) begin
                        if (x < SOURCE_WIDTH+1) x <= x + 1'b1;
                        else line_error <= 1;
                    end
                    if (sample_pixel) begin
                        sample_count <= sample_count + 1'b1;
                    end
                end
            end
        end
    end
endmodule
