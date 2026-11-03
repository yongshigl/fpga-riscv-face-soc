`timescale 1ns/1ps

// Stores raw OV5640 RGB565 frames (1280x720) into one of two DDR frame buffers.
// The CPU can lock one bank so that the frame used for detection stays intact
// while later frames continue into the other bank.
module camera_frame_writer #(
    parameter integer SRC_WIDTH = 1280,
    parameter integer SRC_HEIGHT = 720,
    parameter [31:0] BANK0_BASE = 32'h83000000,
    parameter [31:0] BANK1_BASE = 32'h83400000,
    parameter integer LINE_BYTES = 32,
    parameter integer WARMUP_FRAMES = 10,
    parameter integer CAPTURE_CONTROL = 0
) (
    input wire pixel_clk, pixel_rst_n,
    input wire configured,
    input wire capture_enable,
    input wire vsync, href, pixel_valid,
    input wire [15:0] pixel,
    input wire frame_lock_valid,
    input wire frame_lock_bank,
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
    output reg last_bank,
    output reg [31:0] completed_frames,
    output reg [31:0] dropped_lines,
    output reg [31:0] frame_pixels
);
    localparam integer LINE_PIXELS = LINE_BYTES / 2;
    localparam integer ROW_BYTES = SRC_WIDTH * 2;
    localparam integer FRAME_BYTES = ROW_BYTES * SRC_HEIGHT;
    localparam integer QUEUE_DEPTH = 4;

    initial begin
        if (ROW_BYTES % LINE_BYTES != 0) begin
            $display("camera_frame_writer: row bytes must be a multiple of LINE_BYTES");
            $finish;
        end
    end

    (* ASYNC_REG = "TRUE" *) reg [1:0] configured_sync;
    always @(posedge pixel_clk or negedge pixel_rst_n) begin
        if (!pixel_rst_n) configured_sync <= 0;
        else configured_sync <= {configured_sync[0], configured};
    end
    wire active = configured_sync[1];

    reg capturing;
    reg [11:0] x;
    reg [10:0] y;
    reg vsync_last, href_last;
    reg [4:0] frame_count;
    wire stream_ready = frame_count >= WARMUP_FRAMES;
    reg write_bank;
    reg skip_frame;
    (* ASYNC_REG = "TRUE" *) reg [1:0] lock_valid_sync;
    (* ASYNC_REG = "TRUE" *) reg [1:0] lock_bank_sync;
    wire locked = lock_valid_sync[1];
    wire locked_bank = lock_bank_sync[1];
    wire [31:0] bank_base = write_bank ? BANK1_BASE : BANK0_BASE;

    reg [255:0] word;
    reg [3:0] pixel_in_line;
    reg [31:0] line_addr;
    wire sample = capturing && active && stream_ready && !skip_frame &&
                  href && pixel_valid && x < SRC_WIDTH && y < SRC_HEIGHT;

    // Entry queue: (address, data) pairs waiting for the CDC handshake.
    reg [255:0] q0_data, q1_data;
    reg [31:0] q0_addr, q1_addr;
    reg q0_valid, q1_valid;
    wire queue_full = q0_valid && q1_valid;
    wire queue_empty = !q0_valid;
    reg queue_push;
    reg [255:0] push_data;
    reg [31:0] push_addr;
    reg marker_pending;

    reg entry_req;
    reg [255:0] entry_data;
    reg [31:0] entry_addr;
    (* ASYNC_REG = "TRUE" *) reg [1:0] req_sync_mem;
    (* ASYNC_REG = "TRUE" *) reg [1:0] ack_sync_pix;
    reg ack_level;
    wire entry_busy = (entry_req != ack_sync_pix[1]);
    reg entry_marker;
    reg end_pending;

    always @(posedge pixel_clk or negedge pixel_rst_n) begin
        if (!pixel_rst_n) begin
            capturing <= 0; x <= 0; y <= 0; vsync_last <= 1; href_last <= 0;
            frame_count <= 0; skip_frame <= 0;
            lock_valid_sync <= 0; lock_bank_sync <= 0;
            word <= 0; pixel_in_line <= 0; line_addr <= 0;
            q0_data <= 0; q1_data <= 0; q0_addr <= 0; q1_addr <= 0;
            q0_valid <= 0; q1_valid <= 0;
            queue_push <= 0; push_data <= 0; push_addr <= 0;
            entry_req <= 0; entry_data <= 0; entry_addr <= 0; entry_marker <= 0;
            end_pending <= 0; ack_sync_pix <= 0; dropped_lines <= 0;
            frame_pixels <= 0;
            marker_pending <= 0;
        end else begin
            vsync_last <= vsync;
            href_last <= href;
            ack_sync_pix <= {ack_sync_pix[0], ack_level};
            lock_valid_sync <= {lock_valid_sync[0], frame_lock_valid};
            lock_bank_sync <= {lock_bank_sync[0], frame_lock_bank};
            queue_push <= 0;

            if (vsync_last && !vsync) begin
                capturing <= !CAPTURE_CONTROL || capture_enable; x <= 0; y <= 0;
                if (frame_count != 5'd31) frame_count <= frame_count + 1'b1;
                // The frame starting now goes to the current bank unless locked.
                skip_frame <= locked && (write_bank == locked_bank);
                word <= 0; pixel_in_line <= 0; line_addr <= bank_base;
            end else if (!vsync_last && vsync) begin
                capturing <= 0;
                // Completion is signalled through the memory domain (END marker).
            end

            if (sample) begin
                frame_pixels <= frame_pixels + 1'b1;
                x <= x + 1'b1;
                word <= {{pixel[7:0], pixel[15:8]}, word[255:16]};
                if (pixel_in_line == LINE_PIXELS - 1) begin
                    pixel_in_line <= 0;
                    queue_push <= 1;
                    push_data <= {{pixel[7:0], pixel[15:8]}, word[255:16]};
                    push_addr <= line_addr;
                    line_addr <= line_addr + LINE_BYTES;
                end else begin
                    pixel_in_line <= pixel_in_line + 1'b1;
                end
            end else if (href_last && !href) begin
                x <= 0;
                if (y < SRC_HEIGHT) y <= y + 1'b1;
                word <= 0; pixel_in_line <= 0;
                if (capturing && y == SRC_HEIGHT - 1 && !skip_frame && stream_ready) begin
                    // Queue the END marker one cycle after the final line push.
                    marker_pending <= 1;
                end
            end

            // One queue operation per cycle: drain first, then marker, then data.
            if (!queue_empty && !entry_busy) begin
                entry_data <= q0_data;
                entry_addr <= q0_addr;
                entry_marker <= q0_addr == 32'hFFFFFFFF;
                entry_req <= ~entry_req;
                if (queue_push && q1_valid) begin
                    q0_data <= q1_data; q0_addr <= q1_addr; q0_valid <= 1;
                    q1_data <= push_data; q1_addr <= push_addr; q1_valid <= 1;
                end else if (queue_push) begin
                    q0_data <= push_data; q0_addr <= push_addr; q0_valid <= 1;
                    q1_valid <= 0;
                end else begin
                    q0_data <= q1_data; q0_addr <= q1_addr; q0_valid <= q1_valid;
                    q1_valid <= 0;
                end
            end else if (marker_pending) begin
                if (!q0_valid) begin
                    q0_data <= 0; q0_addr <= 32'hFFFFFFFF; q0_valid <= 1;
                    marker_pending <= 0; end_pending <= 1;
                end else if (!q1_valid) begin
                    q1_data <= 0; q1_addr <= 32'hFFFFFFFF; q1_valid <= 1;
                    marker_pending <= 0; end_pending <= 1;
                end
            end else if (queue_push) begin
                if (!q0_valid) begin
                    q0_data <= push_data; q0_addr <= push_addr; q0_valid <= 1;
                end else if (!q1_valid) begin
                    q1_data <= push_data; q1_addr <= push_addr; q1_valid <= 1;
                end else begin
                    dropped_lines <= dropped_lines + 1'b1;
                end
            end
        end
    end

    // ------------------------------------------------ memory clock domain
    reg frame_done_toggle;
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
            frame_done <= 0; frame_sequence <= 0; last_bank <= 0; completed_frames <= 0;
            frame_done_toggle <= 0;
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
                completed_frames <= completed_frames + 1'b1;
                last_bank <= write_bank;
                frame_done_toggle <= ~frame_done_toggle;
            end
        end
    end
    assign mem_req_write = 1'b1;
    assign mem_req_wstrb = {32{1'b1}};
    assign mem_rsp_ready = 1'b1;

    // Bank toggle: after a completed (non-skipped) frame the next frame goes to
    // the other bank. Skipped frames leave the bank unchanged.
    (* ASYNC_REG = "TRUE" *) reg [1:0] done_sync_pix;
    reg done_level_d;
    wire frame_done_sync = done_sync_pix[1] ^ done_level_d;
    always @(posedge pixel_clk or negedge pixel_rst_n) begin
        if (!pixel_rst_n) begin
            done_sync_pix <= 0;
            done_level_d <= 0;
            write_bank <= 0;
        end else begin
            done_sync_pix <= {done_sync_pix[0], frame_done_toggle};
            done_level_d <= done_sync_pix[1];
            if (frame_done_sync) write_bank <= ~write_bank;
        end
    end
endmodule
