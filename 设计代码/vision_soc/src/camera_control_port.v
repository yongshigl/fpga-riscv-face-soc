`timescale 1ns/1ps

module camera_control_port #(
    parameter integer CLOCK_HZ = 27000000,
    parameter integer BAUD_RATE = 921600,
    parameter integer JPEG_QUALITY = 8,
    parameter integer OVERLAP_CAPTURE = 0,
    parameter integer CAPTURE_LEAD_US = 60000,
    parameter integer MAX_SEND_AGE_US = 30000,
    parameter integer RGB_MODE = 0,
    parameter integer CAM1_JPEG = 0,
    parameter integer RGB_PREVIEW_DOWNSAMPLE_BITS = 2
) (
    input wire clk, rst_n,
    input wire req_valid,
    output wire req_ready,
    input wire req_write,
    input wire [31:0] req_addr, req_wdata,
    input wire [3:0] req_wstrb,
    output reg rsp_valid,
    input wire rsp_ready,
    output reg [31:0] rsp_rdata,
    output reg rsp_error,
    input wire camera_clk_0, camera_href_0, camera_vsync_0,
    input wire [7:0] camera_data_0,
    output wire SCL_0,
    inout wire SDA_0,
    output wire cam_rst_0,
    input wire camera_clk_1, camera_href_1, camera_vsync_1,
    input wire [7:0] camera_data_1,
    output wire SCL_1,
    inout wire SDA_1,
    output wire cam_rst_1,
    output wire preview_tx, preview_active,
    output wire [1:0] configured, frames_ready,
    input wire [31:0] tensor_sequence,
    input wire tensor_done_toggle,
    input wire tensor_overflow,
    input wire [31:0] tensor_samples,
    input wire [31:0] tensor_lines,
    input wire [31:0] tensor_groups,
    input wire [31:0] frame_sequence,
    input wire frame_last_bank,
    input wire affine_busy,
    input wire affine_done_toggle,
    input wire [31:0] affine_completed,
    input wire capture_acknowledge_toggle,
    output reg capture_request_toggle,
    output reg [31:0] affine_a,
    output reg [31:0] affine_b,
    output reg [31:0] affine_c,
    output reg [31:0] affine_d,
    output reg [31:0] affine_e,
    output reg [31:0] affine_f,
    output reg [31:0] affine_src_base,
    output reg affine_start_toggle,
    output reg frame_lock_valid,
    output reg frame_lock_bank
);
    (* ASYNC_REG = "TRUE" *) reg [1:0] affine_done_sync;
    (* ASYNC_REG = "TRUE" *) reg [1:0] affine_busy_sync;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            affine_done_sync <= 0;
            affine_busy_sync <= 0;
        end else begin
            affine_done_sync <= {affine_done_sync[0], affine_done_toggle};
            affine_busy_sync <= {affine_busy_sync[0], affine_busy};
        end
    end
    wire affine_pending = affine_start_toggle != affine_done_sync[1];
    (* ASYNC_REG = "TRUE" *) reg [1:0] capture_acknowledge_sync;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) capture_acknowledge_sync <= 0;
        else capture_acknowledge_sync <= {capture_acknowledge_sync[0], capture_acknowledge_toggle};
    end
    wire capture_pending = capture_request_toggle != capture_acknowledge_sync[1];
    (* ASYNC_REG = "TRUE" *) reg [1:0] done_sync;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) done_sync <= 0;
        else done_sync <= {done_sync[0], tensor_done_toggle};
    end
    wire tensor_done_level = done_sync[1];
    reg preview_enable;
    wire [7:0] status_0, status_1, format_0, format_1, isp_0, isp_1;
    wire [7:0] pattern_0, pattern_1, control_0, control_1, writes_0, writes_1;
    wire [15:0] id_0, id_1, width_0, width_1, height_0, height_1;
    wire [15:0] nacks_0, nacks_1, address_0, address_1;
    ov5640_control #(.CLOCK_HZ(CLOCK_HZ), .NATIVE_JPEG(RGB_MODE ? 0 : 1), .JPEG_QUALITY(JPEG_QUALITY)) sensor_0 (
        .clk(clk), .rst_n(rst_n), .scl(SCL_0), .sda(SDA_0), .camera_rst_n(cam_rst_0),
        .init_done(configured[0]), .status_code(status_0), .chip_id(id_0), .format(format_0),
        .isp_format(isp_0), .test_pattern(pattern_0), .system_control(control_0),
        .width(width_0), .height(height_0), .nack_count(nacks_0), .last_address(address_0), .write_count(writes_0)
    );
    ov5640_control #(.CLOCK_HZ(CLOCK_HZ), .NATIVE_JPEG(CAM1_JPEG ? 1 : (RGB_MODE ? 0 : 1)),
        .JPEG_QUALITY(JPEG_QUALITY)) sensor_1 (
        .clk(clk), .rst_n(rst_n), .scl(SCL_1), .sda(SDA_1), .camera_rst_n(cam_rst_1),
        .init_done(configured[1]), .status_code(status_1), .chip_id(id_1), .format(format_1),
        .isp_format(isp_1), .test_pattern(pattern_1), .system_control(control_1),
        .width(width_1), .height(height_1), .nack_count(nacks_1), .last_address(address_1), .write_count(writes_1)
    );
    wire [127:0] diagnostic_0 = {status_0, id_0, format_0, isp_0, pattern_0, control_0,
        width_0, height_0, nacks_0, address_0, writes_0};
    wire [127:0] diagnostic_1 = {status_1, id_1, format_1, isp_1, pattern_1, control_1,
        width_1, height_1, nacks_1, address_1, writes_1};
    wire [7:0] tx_data;
    wire tx_valid, tx_ready;
    localparam [31:0] BUILD_ID = CAM1_JPEG ? 32'h26100501 :
        (OVERLAP_CAPTURE != 0 ? 32'h26100401 : 32'h26100106);
    camera_native_stream #(.CLOCK_HZ(CLOCK_HZ), .BAUD_RATE(BAUD_RATE), .RGB_MODE(RGB_MODE),
        .CAM1_JPEG(CAM1_JPEG), .DOWNSAMPLE_BITS(RGB_PREVIEW_DOWNSAMPLE_BITS),
        .OVERLAP_CAPTURE(OVERLAP_CAPTURE), .CAPTURE_LEAD_US(CAPTURE_LEAD_US),
        .MAX_SEND_AGE_US(MAX_SEND_AGE_US), .BUILD_ID(BUILD_ID)) stream (
        .sys_clk(clk), .rst_n(rst_n), .enable(preview_enable), .active(preview_active),
        .configured_0(configured[0]), .configured_1(configured[1]),
        .diagnostic_base_0(diagnostic_0), .diagnostic_base_1(diagnostic_1),
        .camera_clk_0(camera_clk_0), .camera_href_0(camera_href_0), .camera_vsync_0(camera_vsync_0),
        .camera_data_0(camera_data_0), .camera_clk_1(camera_clk_1), .camera_href_1(camera_href_1),
        .camera_vsync_1(camera_vsync_1), .camera_data_1(camera_data_1),
        .tx_data(tx_data), .tx_valid(tx_valid), .tx_ready(tx_ready), .frames_ready(frames_ready)
    );
    uart_tx #(.CLOCK_HZ(CLOCK_HZ), .BAUD_RATE(BAUD_RATE)) serial_tx (
        .clk(clk), .rst_n(rst_n), .data(tx_data), .valid(tx_valid),
        .ready(tx_ready), .tx(preview_tx), .busy()
    );

    assign req_ready = !rsp_valid;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            preview_enable <= 0;
            affine_a <= 0; affine_b <= 0; affine_c <= 0;
            affine_d <= 0; affine_e <= 0; affine_f <= 0;
            affine_src_base <= 0; affine_start_toggle <= 0;
            frame_lock_valid <= 0; frame_lock_bank <= 0;
            capture_request_toggle <= 0;
            rsp_valid <= 0;
            rsp_rdata <= 0;
            rsp_error <= 0;
        end else begin
            if (rsp_valid && rsp_ready) rsp_valid <= 0;
            if (req_valid && req_ready) begin
                rsp_valid <= 1;
                rsp_rdata <= 0;
                rsp_error <= 0;
                if ((req_addr & 32'hffffff03) != 32'h21008000) rsp_error <= 1;
                else if (req_write) begin
                    case (req_addr[7:2])
                        0: if (!req_wstrb[0] || req_wdata[31:1] != 0) rsp_error <= 1;
                           else preview_enable <= req_wdata[0];
                        26: affine_a <= req_wdata;
                        27: affine_b <= req_wdata;
                        28: affine_c <= req_wdata;
                        29: affine_d <= req_wdata;
                        30: affine_e <= req_wdata;
                        31: affine_f <= req_wdata;
                        32: affine_src_base <= req_wdata;
                        33: if (req_wdata[0]) affine_start_toggle <= ~affine_start_toggle;
                        35: begin
                            frame_lock_valid <= req_wdata[0];
                            frame_lock_bank <= req_wdata[1];
                        end
                        37: if (!RGB_MODE || req_wdata != 1 || req_wstrb != 4'hf || capture_pending)
                                rsp_error <= 1;
                            else capture_request_toggle <= ~capture_request_toggle;
                        default: rsp_error <= 1;
                    endcase
                end else case (req_addr[7:2])
                    0: rsp_rdata <= {31'd0, preview_enable};
                    1: rsp_rdata <= {27'd0, frames_ready, configured, preview_active};
                    2: rsp_rdata <= BUILD_ID;
                    3: rsp_rdata <= 0;
                    4: rsp_rdata <= diagnostic_0[127:96];
                    5: rsp_rdata <= diagnostic_0[95:64];
                    6: rsp_rdata <= diagnostic_0[63:32];
                    7: rsp_rdata <= diagnostic_0[31:0];
                    8: rsp_rdata <= diagnostic_1[127:96];
                    9: rsp_rdata <= diagnostic_1[95:64];
                    10: rsp_rdata <= diagnostic_1[63:32];
                    11: rsp_rdata <= diagnostic_1[31:0];
                    12: rsp_rdata <= RGB_MODE ? {16'd180, 16'd320} : {16'd360, 16'd640};
                    13: rsp_rdata <= JPEG_QUALITY;
                    14: rsp_rdata <= BAUD_RATE;
                    15: rsp_rdata <= CLOCK_HZ;
                    16: rsp_rdata <= tensor_sequence;
                    17: rsp_rdata <= {29'd0, RGB_MODE, tensor_overflow, tensor_done_level};
                    18: rsp_rdata <= RGB_MODE ? 32'h81000000 : 32'd0;
                    19: rsp_rdata <= RGB_MODE ? {16'd192, 16'd320} : 32'd0;
                    20: rsp_rdata <= tensor_samples;
                    21: rsp_rdata <= tensor_lines;
                    22: rsp_rdata <= tensor_groups;
                    26: rsp_rdata <= affine_a;
                    27: rsp_rdata <= affine_b;
                    28: rsp_rdata <= affine_c;
                    29: rsp_rdata <= affine_d;
                    30: rsp_rdata <= affine_e;
                    31: rsp_rdata <= affine_f;
                    32: rsp_rdata <= affine_src_base;
                    33: rsp_rdata <= {30'd0, affine_done_sync[1], affine_busy_sync[1] || affine_pending};
                    34: rsp_rdata <= frame_sequence;
                    35: rsp_rdata <= {29'd0, frame_last_bank, frame_lock_bank, frame_lock_valid};
                    36: rsp_rdata <= affine_completed;
                    37: rsp_rdata <= {30'd0, capture_acknowledge_sync[1], capture_pending};
                    default: rsp_error <= 1;
                endcase
            end
        end
    end
endmodule
