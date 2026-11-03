`timescale 1ns/1ps

// Warps a rectangle of the raw RGB565 frame buffer into the MobileFaceNet
// 112x112 input tensor (NCHW, row_stride 128, plane_stride 14336).
// Coefficients are signed Q16.16 destination->source:
//   sx = a*x + b*y + c ; sy = d*x + e*y + f
// Sampling is bilinear with 16-bit fractions. Each output row is processed as
// 128 pixels: bytes 0..111 from the warp, bytes 112..127 zero-point 128, so every
// row is exactly four 32-byte line writes per plane.
module camera_affine_resampler #(
    parameter integer OUT_WIDTH = 112,
    parameter integer ROW_PIXELS = 128,
    parameter integer OUT_HEIGHT = 112,
    parameter integer ROW_STRIDE = 128,
    parameter integer PLANE_STRIDE = 14336,
    parameter [31:0] OUT_BASE = 32'h82000000,
    parameter integer SRC_WIDTH = 1280,
    parameter integer SRC_HEIGHT = 720,
    parameter integer SRC_ROW_BYTES = 2560,
    parameter integer LINE_BYTES = 32,
    parameter integer SKIP_ZERO_WEIGHT_READS = 1
) (
    input wire clk, rst_n,
    input wire start_toggle,
    input wire signed [31:0] coord_a, coord_b, coord_c,
    input wire signed [31:0] coord_d, coord_e, coord_f,
    input wire [31:0] source_base,
    output reg busy,
    output reg done_toggle,
    output reg [31:0] completed,
    output reg mem_req_valid,
    input wire mem_req_ready,
    output reg mem_req_write,
    output reg [31:0] mem_req_addr,
    output reg [255:0] mem_req_wdata,
    output reg [31:0] mem_req_wstrb,
    input wire mem_rsp_valid,
    output wire mem_rsp_ready,
    input wire [255:0] mem_rsp_rdata,
    input wire mem_rsp_error
);
    localparam [31:0] PLANE0 = OUT_BASE + 2 * PLANE_STRIDE;
    localparam [31:0] PLANE1 = OUT_BASE + PLANE_STRIDE;
    localparam [31:0] PLANE2 = OUT_BASE;

    (* ASYNC_REG = "TRUE" *) reg [1:0] start_sync;
    reg start_level_d;
    wire start_event = start_sync[1] ^ start_level_d;
    reg signed [31:0] ca, cb, cc, cd, ce, cf;
    reg [31:0] src_base;

    localparam IDLE = 0, COORD = 1, RD0_I = 2, RD0_W = 3, RD1_I = 4, RD1_W = 5,
               RD2_I = 6, RD2_W = 7, RD3_I = 8, RD3_W = 9, HORIZ = 10,
               HORIZ_SUM = 11, VERT_MUL = 12, VERT_SUM = 13,
               WR_ISSUE = 14, WR_WAIT = 15, ADDRESS_ROW = 16;
    reg [4:0] state;
    reg [7:0] ox, oy;
    reg [5:0] asm_count;      // byte position within the current 32-byte line

    reg signed [31:0] sx, sy;
    reg [31:0] source_row_address;
    reg [255:0] fetched0, fetched1, fetched2, fetched3;
    reg [24:0] h0r_q, h0g_q, h0b_q, h1r_q, h1g_q, h1b_q;
    reg [24:0] hx0r_q, hx0g_q, hx0b_q, hx1r_q, hx1g_q, hx1b_q;
    reg [24:0] hx2r_q, hx2g_q, hx2b_q, hx3r_q, hx3g_q, hx3b_q;
    reg [41:0] vx0r_q, vx0g_q, vx0b_q, vx1r_q, vx1g_q, vx1b_q;
    reg [1:0] wr_phase;
    reg [2:0] push_index;

    reg [255:0] asm0, asm1, asm2;
    reg [255:0] wr0, wr1, wr2;
    reg [31:0] wr_a0, wr_a1, wr_a2;

    reg [1:0] reset_sync;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) reset_sync <= 0;
        else reset_sync <= {reset_sync[0], 1'b1};
    end
    wire ready = reset_sync[1];

    function [23:0] expand565;
        input [15:0] value;
        reg [4:0] r5;
        reg [5:0] g6;
        reg [4:0] b5;
        begin
            r5 = value[15:11]; g6 = value[10:5]; b5 = value[4:0];
            expand565 = {{r5, r5[4:2]}, {g6, g6[5:4]}, {b5, b5[4:2]}};
        end
    endfunction

    // Row starts use two multipliers; within a row the affine transform is a
    // pair of constant increments. This removes two wide multipliers and the
    // long multiply-add chain from the per-pixel path.
    wire signed [31:0] row_sx = cb * oy + cc;
    wire signed [31:0] row_sy = ce * oy + cf;
    wire signed [31:0] sx_floor = sx >>> 16;
    wire signed [31:0] sy_floor = sy >>> 16;
    wire [15:0] fx = (sx_floor < 0 || sx_floor >= SRC_WIDTH - 1) ? 16'd0 : sx[15:0];
    wire [15:0] fy = (sy_floor < 0 || sy_floor >= SRC_HEIGHT - 1) ? 16'd0 : sy[15:0];
    wire signed [31:0] sx_clamped = (sx_floor < 0) ? 0 :
        (sx_floor > SRC_WIDTH - 1 ? SRC_WIDTH - 1 : sx_floor);
    wire signed [31:0] sy_clamped = (sy_floor < 0) ? 0 :
        (sy_floor > SRC_HEIGHT - 1 ? SRC_HEIGHT - 1 : sy_floor);
    wire [31:0] sx_int = sx_clamped[31:0];
    wire [31:0] sy_int = sy_clamped[31:0];
    wire [16:0] weight_x0 = 17'd65536 - {1'b0, fx};
    wire [16:0] weight_x1 = {1'b0, fx};
    wire [16:0] weight_y0 = 17'd65536 - {1'b0, fy};
    wire [16:0] weight_y1 = {1'b0, fy};
    wire [31:0] row0_addr = source_row_address;
    wire [31:0] row1_addr = sy_int == SRC_HEIGHT - 1 ? row0_addr : row0_addr + SRC_ROW_BYTES;
    wire [31:0] line0_addr = row0_addr + ((sx_int >> 4) << 5);
    wire [31:0] line1_addr = row1_addr + ((sx_int >> 4) << 5);
    wire need_second = sx_int[3:0] == 4'hF && sx_int != SRC_WIDTH - 1 &&
        (SKIP_ZERO_WEIGHT_READS == 0 || fx != 0);
    wire need_bottom = SKIP_ZERO_WEIGHT_READS == 0 || fy != 0;
    wire [3:0] px = sx_int[3:0];
    wire [3:0] px1 = sx_int == SRC_WIDTH - 1 ? px : px + 1'b1;

    // The frame buffer stores the DVP byte order (high byte first); rebuild the
    // 16-bit RGB565 value by swapping the two bytes of each slice.
    wire [15:0] r00 = fetched0[px * 16 +: 16];
    wire [15:0] r10 = need_second ? fetched1[px1 * 16 +: 16] : fetched0[px1 * 16 +: 16];
    wire [15:0] r01 = fetched2[px * 16 +: 16];
    wire [15:0] r11 = need_second ? fetched3[px1 * 16 +: 16] : fetched2[px1 * 16 +: 16];
    wire [15:0] p00 = {r00[7:0], r00[15:8]};
    wire [15:0] p10 = {r10[7:0], r10[15:8]};
    wire [15:0] p01 = {r01[7:0], r01[15:8]};
    wire [15:0] p11 = {r11[7:0], r11[15:8]};
    wire [23:0] e00 = expand565(p00);
    wire [23:0] e10 = expand565(p10);
    wire [23:0] e01 = expand565(p01);
    wire [23:0] e11 = expand565(p11);
    wire [41:0] vr = vx0r_q + vx1r_q;
    wire [41:0] vg = vx0g_q + vx1g_q;
    wire [41:0] vb = vx0b_q + vx1b_q;
    wire [7:0] out_r = (vr + 42'd2147483648) >> 32;
    wire [7:0] out_g = (vg + 42'd2147483648) >> 32;
    wire [7:0] out_b = (vb + 42'd2147483648) >> 32;
    wire [7:0] quant_r = out_r == 8'hff ? 8'hff : out_r + {7'd0, out_r[0]};
    wire [7:0] quant_g = out_g == 8'hff ? 8'hff : out_g + {7'd0, out_g[0]};
    wire [7:0] quant_b = out_b == 8'hff ? 8'hff : out_b + {7'd0, out_b[0]};

    wire [31:0] wr_line_offset = {24'd0, ox[7:5]} * LINE_BYTES;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            start_sync <= 0; start_level_d <= 0;
            ca <= 0; cb <= 0; cc <= 0; cd <= 0; ce <= 0; cf <= 0; src_base <= 0;
            state <= IDLE; ox <= 0; oy <= 0; asm_count <= 0;
            sx <= 0; sy <= 0;
            source_row_address <= 0;
            fetched0 <= 0; fetched1 <= 0; fetched2 <= 0; fetched3 <= 0;
            h0r_q <= 0; h0g_q <= 0; h0b_q <= 0; h1r_q <= 0; h1g_q <= 0; h1b_q <= 0;
            hx0r_q <= 0; hx0g_q <= 0; hx0b_q <= 0; hx1r_q <= 0; hx1g_q <= 0; hx1b_q <= 0;
            hx2r_q <= 0; hx2g_q <= 0; hx2b_q <= 0; hx3r_q <= 0; hx3g_q <= 0; hx3b_q <= 0;
            vx0r_q <= 0; vx0g_q <= 0; vx0b_q <= 0; vx1r_q <= 0; vx1g_q <= 0; vx1b_q <= 0;
            asm0 <= 0; asm1 <= 0; asm2 <= 0;
            wr0 <= 0; wr1 <= 0; wr2 <= 0; wr_a0 <= 0; wr_a1 <= 0; wr_a2 <= 0;
            wr_phase <= 0; push_index <= 0;
            mem_req_valid <= 0; mem_req_write <= 0; mem_req_addr <= 0;
            mem_req_wdata <= 0; mem_req_wstrb <= 0;
            busy <= 0; done_toggle <= 0; completed <= 0;
        end else begin
            start_sync <= {start_sync[0], start_toggle};
            start_level_d <= start_sync[1];

            if (start_event && state == IDLE && ready) begin
                ca <= coord_a; cb <= coord_b; cc <= coord_c;
                cd <= coord_d; ce <= coord_e; cf <= coord_f;
                src_base <= source_base;
                ox <= 0; oy <= 0; asm_count <= 0;
                asm0 <= 0; asm1 <= 0; asm2 <= 0;
                busy <= 1;
                state <= COORD;
            end

            case (state)
                COORD: begin
                    if (ox == 0) begin
                        sx <= row_sx;
                        sy <= row_sy;
                    end else begin
                        sx <= sx + ca;
                        sy <= sy + cd;
                    end
                    if (ox >= OUT_WIDTH) state <= HORIZ;
                    else state <= ADDRESS_ROW;
                end
                ADDRESS_ROW: begin
                    source_row_address <= src_base + sy_int * SRC_ROW_BYTES;
                    state <= RD0_I;
                end
                RD0_I: begin
                    mem_req_valid <= 1; mem_req_write <= 0; mem_req_addr <= line0_addr;
                    state <= RD0_W;
                end
                RD0_W: begin
                    if (mem_req_ready) mem_req_valid <= 0;
                    if (mem_rsp_valid) begin
                        fetched0 <= mem_rsp_rdata;
                        state <= need_second ? RD1_I : need_bottom ? RD2_I : HORIZ;
                    end
                end
                RD1_I: begin
                    mem_req_valid <= 1; mem_req_write <= 0; mem_req_addr <= line0_addr + LINE_BYTES;
                    state <= RD1_W;
                end
                RD1_W: begin
                    if (mem_req_ready) mem_req_valid <= 0;
                    if (mem_rsp_valid) begin
                        fetched1 <= mem_rsp_rdata;
                        state <= need_bottom ? RD2_I : HORIZ;
                    end
                end
                RD2_I: begin
                    mem_req_valid <= 1; mem_req_write <= 0; mem_req_addr <= line1_addr;
                    state <= RD2_W;
                end
                RD2_W: begin
                    if (mem_req_ready) mem_req_valid <= 0;
                    if (mem_rsp_valid) begin
                        fetched2 <= mem_rsp_rdata;
                        state <= need_second ? RD3_I : HORIZ;
                    end
                end
                RD3_I: begin
                    mem_req_valid <= 1; mem_req_write <= 0; mem_req_addr <= line1_addr + LINE_BYTES;
                    state <= RD3_W;
                end
                RD3_W: begin
                    if (mem_req_ready) mem_req_valid <= 0;
                    if (mem_rsp_valid) begin
                        fetched3 <= mem_rsp_rdata;
                        state <= HORIZ;
                    end
                end
                HORIZ: begin
                    hx0r_q <= e00[23:16] * weight_x0;
                    hx0g_q <= e00[15:8] * weight_x0;
                    hx0b_q <= e00[7:0] * weight_x0;
                    hx1r_q <= e10[23:16] * weight_x1;
                    hx1g_q <= e10[15:8] * weight_x1;
                    hx1b_q <= e10[7:0] * weight_x1;
                    hx2r_q <= e01[23:16] * weight_x0;
                    hx2g_q <= e01[15:8] * weight_x0;
                    hx2b_q <= e01[7:0] * weight_x0;
                    hx3r_q <= e11[23:16] * weight_x1;
                    hx3g_q <= e11[15:8] * weight_x1;
                    hx3b_q <= e11[7:0] * weight_x1;
                    state <= HORIZ_SUM;
                end
                HORIZ_SUM: begin
                    h0r_q <= hx0r_q + hx1r_q;
                    h0g_q <= hx0g_q + hx1g_q;
                    h0b_q <= hx0b_q + hx1b_q;
                    h1r_q <= hx2r_q + hx3r_q;
                    h1g_q <= hx2g_q + hx3g_q;
                    h1b_q <= hx2b_q + hx3b_q;
                    state <= VERT_MUL;
                end
                VERT_MUL: begin
                    vx0r_q <= h0r_q * weight_y0;
                    vx0g_q <= h0g_q * weight_y0;
                    vx0b_q <= h0b_q * weight_y0;
                    vx1r_q <= h1r_q * weight_y1;
                    vx1g_q <= h1g_q * weight_y1;
                    vx1b_q <= h1b_q * weight_y1;
                    state <= VERT_SUM;
                end
                VERT_SUM: begin
                    if (ox >= OUT_WIDTH) begin
                        asm0 <= {8'd128, asm0[255:8]};
                        asm1 <= {8'd128, asm1[255:8]};
                        asm2 <= {8'd128, asm2[255:8]};
                    end else begin
                        asm0 <= {quant_r, asm0[255:8]};
                        asm1 <= {quant_g, asm1[255:8]};
                        asm2 <= {quant_b, asm2[255:8]};
                    end
                    asm_count <= asm_count + 1'b1;
                    if (asm_count == 6'd31) begin
                        wr0 <= ox >= OUT_WIDTH ? {8'd128, asm0[255:8]} : {quant_r, asm0[255:8]};
                        wr1 <= ox >= OUT_WIDTH ? {8'd128, asm1[255:8]} : {quant_g, asm1[255:8]};
                        wr2 <= ox >= OUT_WIDTH ? {8'd128, asm2[255:8]} : {quant_b, asm2[255:8]};
                        wr_a0 <= PLANE0 + (oy * ROW_STRIDE) + wr_line_offset;
                        wr_a1 <= PLANE1 + (oy * ROW_STRIDE) + wr_line_offset;
                        wr_a2 <= PLANE2 + (oy * ROW_STRIDE) + wr_line_offset;
                        wr_phase <= 0;
                        asm_count <= 0;
                        state <= WR_ISSUE;
                    end else begin
                        ox <= ox + 1'b1;
                        state <= COORD;
                    end
                end
                WR_ISSUE: begin
                    mem_req_valid <= 1;
                    mem_req_write <= 1;
                    mem_req_wstrb <= {32{1'b1}};
                    mem_req_wdata <= (wr_phase == 0) ? wr0 : (wr_phase == 1) ? wr1 : wr2;
                    mem_req_addr <= (wr_phase == 0) ? wr_a0 : (wr_phase == 1) ? wr_a1 : wr_a2;
                    state <= WR_WAIT;
                end
                WR_WAIT: begin
                    if (mem_req_ready) mem_req_valid <= 0;
                    if (mem_rsp_valid) begin
                        if (wr_phase == 2) begin
                            if (oy == OUT_HEIGHT - 1 && ox == ROW_PIXELS - 1) begin
                                busy <= 0;
                                done_toggle <= ~done_toggle;
                                completed <= completed + 1'b1;
                                state <= IDLE;
                            end else begin
                                if (ox == ROW_PIXELS - 1) begin
                                    ox <= 0;
                                    oy <= oy + 1'b1;
                                end else begin
                                    ox <= ox + 1'b1;
                                end
                                state <= COORD;
                            end
                        end else begin
                            wr_phase <= wr_phase + 1'b1;
                            state <= WR_ISSUE;
                        end
                    end
                end
                default: ;
            endcase
        end
    end
    assign mem_rsp_ready = 1'b1;
endmodule
