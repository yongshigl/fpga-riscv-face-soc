`timescale 1ns/1ps

// Streams OV5640 RGB565 DVP pixels into the quantized NCHW detector input
// tensor in DDR. Geometry matches the host letterbox exactly for the
// 1280x720 -> 320x192 (active 320x180) detector input:
//   * 4x4 box average per output pixel (cv2.INTER_AREA on a 4x integer step,
//     round-half-to-even like cvRound)
//   * RGB565 expanded to BGR888 planes by bit replication
//   * NCHW planes, row_stride = OUT_WIDTH, plane_stride = OUT_WIDTH*OUT_HEIGHT
//   * bottom padding rows (OUT_ACTIVE_HEIGHT..OUT_HEIGHT-1) written as zeros
//   * each 32-byte line is one 256-bit DDR line write (little-endian lanes).
//
// Line entries cross from the pixel clock to the memory clock with a level
// handshake (one entry at a time). The entry rate is one per ~42 us, so the
// handshake cost is negligible and no FIFO is required.
module camera_tensor_writer #(
    parameter integer SRC_WIDTH = 1280,
    parameter integer SRC_HEIGHT = 720,
    parameter integer OUT_WIDTH = 320,
    parameter integer OUT_ACTIVE_HEIGHT = 180,
    parameter integer OUT_HEIGHT = 192,
    parameter integer PLANE_STRIDE = OUT_WIDTH * OUT_HEIGHT,
    parameter [31:0] BASE_ADDR = 32'h81000000,
    parameter integer LINE_BYTES = 32,
    parameter integer CAPTURE_CONTROL = 0
) (
    input wire pixel_clk, pixel_rst_n,
    input wire configured,
    input wire capture_enable,
    input wire vsync, href, pixel_valid,
    input wire [15:0] pixel,
    input wire mem_clk, mem_rst_n,
    output reg mem_req_valid,
    input wire mem_req_ready,
    output wire mem_req_write,
    output reg [31:0] mem_req_addr,
    output reg [255:0] mem_req_wdata,
    output wire [31:0] mem_req_wstrb,
    input wire mem_rsp_valid,
    output wire mem_rsp_ready,
    input wire [255:0] mem_rsp_rdata,
    input wire mem_rsp_error,
    output reg frame_done,
    output reg [31:0] frame_sequence,
    output reg overflow,
    output reg [31:0] debug_samples,
    output reg [31:0] debug_lines,
    output reg [31:0] debug_groups
);
    localparam integer LINES_PER_ROW = OUT_WIDTH / LINE_BYTES;
    localparam integer PAD_ROWS = OUT_HEIGHT - OUT_ACTIVE_HEIGHT;
    localparam integer COL_BITS = $clog2(OUT_WIDTH);
    localparam [31:0] PLANE0 = BASE_ADDR + 2 * PLANE_STRIDE;
    localparam [31:0] PLANE1 = BASE_ADDR + PLANE_STRIDE;
    localparam [31:0] PLANE2 = BASE_ADDR;

    initial begin
        if (OUT_WIDTH % LINE_BYTES != 0) begin
            $display("camera_tensor_writer: OUT_WIDTH must be a multiple of LINE_BYTES");
            $finish;
        end
    end

    // ------------------------------------------------ pixel clock domain
    (* ASYNC_REG = "TRUE" *) reg [1:0] configured_sync;
    always @(posedge pixel_clk or negedge pixel_rst_n) begin
        if (!pixel_rst_n) configured_sync <= 0;
        else configured_sync <= {configured_sync[0], configured};
    end
    wire active = configured_sync[1];

    reg capturing;
    reg [11:0] x;
    reg [10:0] y;
    reg vsync_last;
    reg href_last;
    reg [4:0] frame_count;
    wire stream_ready = frame_count >= 5'd10;

    wire [7:0] r8 = {pixel[15:11], pixel[15:13]};
    wire [7:0] g8 = {pixel[10:5], pixel[10:9]};
    wire [7:0] b8 = {pixel[4:0], pixel[4:2]};

    reg [9:0] acc_r, acc_g, acc_b;
    wire sample = capturing && active && stream_ready && href && pixel_valid &&
                  x < SRC_WIDTH && y < SRC_HEIGHT;

    reg [35:0] bank0 [0:OUT_WIDTH-1];
    reg [35:0] bank1 [0:OUT_WIDTH-1];
    reg bank_acc;
    wire switch_bank = bank_acc ^ 1'b1;

    reg acc_rd_en;
    reg [COL_BITS-1:0] acc_rd_addr;
    reg [35:0] acc_rd_data;
    reg acc_wr_en;
    reg [COL_BITS-1:0] acc_wr_addr;
    reg [35:0] acc_wr_data;
    reg fin_rd_en;
    reg [COL_BITS-1:0] fin_rd_addr;
    reg [35:0] fin_rd_data;

    always @(posedge pixel_clk) begin
        if (acc_rd_en) acc_rd_data <= bank_acc ? bank1[acc_rd_addr] : bank0[acc_rd_addr];
        if (fin_rd_en) fin_rd_data <= switch_bank ? bank1[fin_rd_addr] : bank0[fin_rd_addr];
        if (acc_wr_en) begin
            if (bank_acc) bank1[acc_wr_addr] <= acc_wr_data;
            else bank0[acc_wr_addr] <= acc_wr_data;
        end
    end

    reg group_pending;
    reg [8:0] group_row;

    localparam FIN_IDLE = 0, FIN_READ = 1, FIN_WAIT = 2, FIN_USE = 3, FIN_PUSH = 4;
    reg [2:0] fin_state;
    reg [COL_BITS-1:0] fin_x;
    reg [4:0] fin_byte;
    reg [8:0] fin_row;
    reg [255:0] line0, line1, line2;
    reg [31:0] addr0, addr1, addr2;

    localparam PUSH_IDLE = 0, PUSH_ONE = 1, PUSH_TWO = 2, PUSH_THREE = 3, PUSH_END = 4;
    reg [2:0] push_state;
    reg [255:0] push0, push1, push2;
    reg [31:0] push_a0, push_a1, push_a2;
    reg push_is_end;

    localparam PAD_IDLE = 0, PAD_ENTRY = 1, PAD_ADVANCE = 2, PAD_FINISH = 3;
    reg [1:0] pad_state;
    reg [7:0] pad_row;
    reg [3:0] pad_col;
    reg [1:0] pad_plane;

    // Entry handshake (pixel -> memory clock).
    reg entry_req;                       // toggles per entry
    reg [255:0] entry_data;
    reg [31:0] entry_addr;
    reg entry_marker;
    (* ASYNC_REG = "TRUE" *) reg [1:0] req_sync_mem;
    (* ASYNC_REG = "TRUE" *) reg [1:0] ack_sync_pix;
    reg ack_level;
    wire entry_busy = (entry_req != ack_sync_pix[1]);

    function [7:0] rne_average;
        input [11:0] sum;
        reg [12:0] adjusted;
        begin
            adjusted = {1'b0, sum} + 13'd7 + {12'd0, sum[4]};
            rne_average = adjusted[11:4];
        end
    endfunction

    always @(posedge pixel_clk or negedge pixel_rst_n) begin
        if (!pixel_rst_n) begin
            capturing <= 0; x <= 0; y <= 0; vsync_last <= 1; href_last <= 0;
            frame_count <= 0;
            acc_r <= 0; acc_g <= 0; acc_b <= 0;
            bank_acc <= 0;
            acc_rd_en <= 0; acc_rd_addr <= 0; acc_wr_en <= 0; acc_wr_addr <= 0; acc_wr_data <= 0;
            fin_rd_en <= 0; fin_rd_addr <= 0;
            group_pending <= 0; group_row <= 0;
            fin_state <= FIN_IDLE; fin_x <= 0; fin_byte <= 0; fin_row <= 0;
            line0 <= 0; line1 <= 0; line2 <= 0; addr0 <= 0; addr1 <= 0; addr2 <= 0;
            push_state <= PUSH_IDLE; push0 <= 0; push1 <= 0; push2 <= 0;
            push_a0 <= 0; push_a1 <= 0; push_a2 <= 0; push_is_end <= 0;
            pad_state <= PAD_IDLE; pad_row <= 0; pad_col <= 0; pad_plane <= 0;
            entry_req <= 0; entry_data <= 0; entry_addr <= 0; entry_marker <= 0;
            ack_sync_pix <= 0;
            overflow <= 0;
            debug_samples <= 0; debug_lines <= 0; debug_groups <= 0;
        end else begin
            vsync_last <= vsync;
            href_last <= href;
            acc_rd_en <= 0;
            acc_wr_en <= 0;
            fin_rd_en <= 0;
            ack_sync_pix <= {ack_sync_pix[0], ack_level};

            if (vsync_last && !vsync) begin
                capturing <= !CAPTURE_CONTROL || capture_enable; x <= 0; y <= 0;
                acc_r <= 0; acc_g <= 0; acc_b <= 0;
                if (frame_count != 5'd31) frame_count <= frame_count + 1'b1;
                group_pending <= 0;
            end else if (!vsync_last && vsync) begin
                capturing <= 0;
            end

            if (sample) begin
                debug_samples <= debug_samples + 1'b1;
                x <= x + 1'b1;
                acc_r <= acc_r + r8;
                acc_g <= acc_g + g8;
                acc_b <= acc_b + b8;
                // Read the accumulator at the start of the 4-pixel group; the
                // data is then ready when the group sum completes.
                if (x[1:0] == 2'd0) begin
                    acc_rd_addr <= x[10:2];
                    acc_rd_en <= 1;
                end
                if (x[1:0] == 2'd3) begin
                    acc_wr_addr <= x[10:2];
                    acc_wr_data <= (y[1:0] == 0) ?
                        {{2'b0, (acc_r + r8)}, {2'b0, (acc_g + g8)}, {2'b0, (acc_b + b8)}} :
                        acc_rd_data + {{2'b0, (acc_r + r8)}, {2'b0, (acc_g + g8)}, {2'b0, (acc_b + b8)}};
                    acc_wr_en <= 1;
                    acc_r <= 0; acc_g <= 0; acc_b <= 0;
                end
            end else if (capturing && href_last && !href) begin
                debug_lines <= debug_lines + 1'b1;
                x <= 0;
                if (y < SRC_HEIGHT) y <= y + 1'b1;
                acc_r <= 0; acc_g <= 0; acc_b <= 0;
            end

            if (capturing && stream_ready && href_last && !href && y[1:0] == 2'd3 && y < SRC_HEIGHT) begin
                group_pending <= 1;
                group_row <= y[10:2];
            end
            if (group_pending && fin_state == FIN_IDLE &&
                push_state == PUSH_IDLE && pad_state == PAD_IDLE && !entry_busy) begin
                debug_groups <= debug_groups + 1'b1;
                group_pending <= 0;
                bank_acc <= ~bank_acc;
                fin_state <= FIN_READ;
                fin_x <= 0;
                fin_byte <= 0;
                fin_row <= group_row;
                line0 <= 0; line1 <= 0; line2 <= 0;
                addr0 <= PLANE0 + ({23'd0, group_row} * OUT_WIDTH);
                addr1 <= PLANE1 + ({23'd0, group_row} * OUT_WIDTH);
                addr2 <= PLANE2 + ({23'd0, group_row} * OUT_WIDTH);
            end

            case (fin_state)
                FIN_READ: begin
                    fin_rd_addr <= fin_x;
                    fin_rd_en <= 1;
                    fin_state <= FIN_WAIT;
                end
                FIN_WAIT: begin
                    fin_state <= FIN_USE;
                end
                FIN_USE: begin
                    line0 <= {rne_average(fin_rd_data[35:24]), line0[255:8]};
                    line1 <= {rne_average(fin_rd_data[23:12]), line1[255:8]};
                    line2 <= {rne_average(fin_rd_data[11:0]), line2[255:8]};
                    fin_byte <= fin_byte + 1'b1;
                    if (fin_byte == LINE_BYTES - 1) begin
                        push0 <= {rne_average(fin_rd_data[35:24]), line0[255:8]};
                        push1 <= {rne_average(fin_rd_data[23:12]), line1[255:8]};
                        push2 <= {rne_average(fin_rd_data[11:0]), line2[255:8]};
                        push_a0 <= addr0; push_a1 <= addr1; push_a2 <= addr2;
                        push_is_end <= 0;
                        push_state <= PUSH_ONE;
                        fin_byte <= 0;
                        fin_state <= FIN_PUSH;
                        if (PAD_ROWS == 0 && fin_x == OUT_WIDTH - 1 && fin_row == OUT_ACTIVE_HEIGHT - 1)
                            push_is_end <= 1;
                    end else begin
                        fin_state <= FIN_READ;
                    end
                    if (fin_x == OUT_WIDTH - 1) fin_x <= 0;
                    else fin_x <= fin_x + 1'b1;
                end
                FIN_PUSH: begin
                    if (push_state == PUSH_IDLE) begin
                        if (fin_x == 0) begin
                            if (fin_row == OUT_ACTIVE_HEIGHT - 1 && PAD_ROWS != 0) begin
                                pad_state <= PAD_ENTRY;
                                pad_row <= 0; pad_col <= 0; pad_plane <= 0;
                            end
                            fin_state <= FIN_IDLE;
                        end else begin
                            fin_state <= FIN_READ;
                        end
                    end
                end
                default: ;
            endcase

            case (push_state)
                PUSH_ONE: if (!entry_busy) begin
                    entry_data <= push0; entry_addr <= push_a0; entry_marker <= 0;
                    entry_req <= ~entry_req;
                    push_state <= PUSH_TWO;
                end
                PUSH_TWO: if (!entry_busy) begin
                    entry_data <= push1; entry_addr <= push_a1; entry_marker <= 0;
                    entry_req <= ~entry_req;
                    push_state <= PUSH_THREE;
                end
                PUSH_THREE: if (!entry_busy) begin
                    entry_data <= push2; entry_addr <= push_a2; entry_marker <= 0;
                    entry_req <= ~entry_req;
                    push_state <= push_is_end ? PUSH_END : PUSH_IDLE;
                    addr0 <= addr0 + LINE_BYTES;
                    addr1 <= addr1 + LINE_BYTES;
                    addr2 <= addr2 + LINE_BYTES;
                end
                PUSH_END: if (!entry_busy) begin
                    entry_data <= 0; entry_addr <= 32'hFFFFFFFF; entry_marker <= 1;
                    entry_req <= ~entry_req;
                    push_state <= PUSH_IDLE;
                end
                default: ;
            endcase

            case (pad_state)
                PAD_ENTRY: begin
                    if (!entry_busy) begin
                        entry_data <= 0;
                        entry_addr <= (pad_plane == 0) ?
                            (PLANE0 + ({23'd0, pad_row} + OUT_ACTIVE_HEIGHT) * OUT_WIDTH + ({26'd0, pad_col} * LINE_BYTES)) :
                            (pad_plane == 1) ?
                            (PLANE1 + ({23'd0, pad_row} + OUT_ACTIVE_HEIGHT) * OUT_WIDTH + ({26'd0, pad_col} * LINE_BYTES)) :
                            (PLANE2 + ({23'd0, pad_row} + OUT_ACTIVE_HEIGHT) * OUT_WIDTH + ({26'd0, pad_col} * LINE_BYTES));
                        entry_marker <= 0;
                        entry_req <= ~entry_req;
                        pad_state <= PAD_ADVANCE;
                    end
                end
                PAD_ADVANCE: begin
                    if (pad_plane != 2) begin
                        pad_plane <= pad_plane + 1'b1;
                        pad_state <= PAD_ENTRY;
                    end else begin
                        pad_plane <= 0;
                        if (pad_col != LINES_PER_ROW - 1) begin
                            pad_col <= pad_col + 1'b1;
                            pad_state <= PAD_ENTRY;
                        end else begin
                            pad_col <= 0;
                            if (pad_row != PAD_ROWS - 1) begin
                                pad_row <= pad_row + 1'b1;
                                pad_state <= PAD_ENTRY;
                            end else begin
                                pad_state <= PAD_FINISH;
                            end
                        end
                    end
                end
                PAD_FINISH: begin
                    if (!entry_busy) begin
                        entry_data <= 0; entry_addr <= 32'hFFFFFFFF; entry_marker <= 1;
                        entry_req <= ~entry_req;
                        pad_state <= PAD_IDLE;
                    end
                end
                default: ;
            endcase
        end
    end

    // ------------------------------------------------ memory clock domain
    reg [1:0] mem_reset;
    reg [31:0] pending;
    reg hold_valid;
    reg [255:0] hold_data;
    reg [31:0] hold_addr;
    reg hold_marker;
    reg end_seen;
    always @(posedge mem_clk or negedge mem_rst_n) begin
        if (!mem_rst_n) mem_reset <= 0;
        else mem_reset <= {mem_reset[0], 1'b1};
    end
    wire mem_ready = mem_reset[1];

    always @(posedge mem_clk or negedge mem_rst_n) begin
        if (!mem_rst_n) begin
            mem_req_valid <= 0; mem_req_addr <= 0; mem_req_wdata <= 0;
            pending <= 0; hold_valid <= 0; hold_marker <= 0; end_seen <= 0;
            req_sync_mem <= 0; ack_level <= 0;
            frame_done <= 0; frame_sequence <= 0;
        end else begin
            frame_done <= 0;
            req_sync_mem <= {req_sync_mem[0], entry_req};
            if (mem_req_valid && mem_req_ready) begin
                mem_req_valid <= 0;
            end
            case ({mem_req_valid && mem_req_ready, mem_rsp_valid})
                2'b10: pending <= pending + 1'b1;
                2'b01: pending <= pending - 1'b1;
                default: ;
            endcase
            // Latch a new entry only when the previous one has been issued.
            if (hold_valid && !mem_req_valid) begin
                if (hold_marker) begin
                    end_seen <= 1;
                    hold_valid <= 0;
                end else begin
                    mem_req_valid <= 1;
                    mem_req_addr <= hold_addr;
                    mem_req_wdata <= hold_data;
                    hold_valid <= 0;
                end
            end
            if (!hold_valid && mem_ready && (req_sync_mem[1] != ack_level)) begin
                hold_valid <= 1;
                hold_addr <= entry_addr;
                hold_data <= entry_data;
                hold_marker <= entry_marker;
                ack_level <= req_sync_mem[1];
            end
            if (end_seen && !mem_req_valid && !hold_valid && pending == 0 && mem_ready) begin
                end_seen <= 0;
                frame_done <= 1;
                frame_sequence <= frame_sequence + 1'b1;
            end
        end
    end
    assign mem_req_write = 1'b1;
    assign mem_req_wstrb = {32{1'b1}};
    assign mem_rsp_ready = 1'b1;
endmodule
