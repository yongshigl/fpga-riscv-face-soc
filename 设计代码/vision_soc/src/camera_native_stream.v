`timescale 1ns/1ps

module camera_native_stream #(
    parameter integer CLOCK_HZ = 27000000,
    parameter integer RGB_MODE = 0,
    parameter integer CAM1_JPEG = 0,
    parameter integer SOURCE_WIDTH = 1280,
    parameter integer SOURCE_HEIGHT = 720,
    parameter integer DOWNSAMPLE_BITS = 2,
    parameter integer WIDTH = RGB_MODE ? SOURCE_WIDTH >> DOWNSAMPLE_BITS : 640,
    parameter integer HEIGHT = RGB_MODE ? SOURCE_HEIGHT >> DOWNSAMPLE_BITS : 360,
    parameter integer JPEG_WIDTH = (RGB_MODE && CAM1_JPEG) ? 640 : WIDTH,
    parameter integer JPEG_HEIGHT = (RGB_MODE && CAM1_JPEG) ? 360 : HEIGHT,
    parameter integer BUFFER_BYTES = 65536,
    parameter integer ADDR_BITS = $clog2(BUFFER_BYTES),
    parameter integer BAUD_RATE = 921600,
    parameter integer OVERLAP_CAPTURE = 0,
    parameter integer CAPTURE_LEAD_US = 60000,
    parameter integer MAX_SEND_AGE_US = 30000,
    parameter [31:0] BUILD_ID = 32'h26100106,
    parameter integer RGB_BUFFER_BANK_BITS = 11
) (
    input wire sys_clk, rst_n, enable,
    output wire active,
    input wire configured_0, configured_1,
    input wire [127:0] diagnostic_base_0, diagnostic_base_1,
    input wire camera_clk_0, camera_href_0, camera_vsync_0,
    input wire [7:0] camera_data_0,
    input wire camera_clk_1, camera_href_1, camera_vsync_1,
    input wire [7:0] camera_data_1,
    output wire [7:0] tx_data,
    output wire tx_valid,
    input wire tx_ready,
    output wire [1:0] frames_ready
);
    localparam integer CAM0_RGB = RGB_MODE;
    localparam integer CAM1_RGB = RGB_MODE && !CAM1_JPEG;
    localparam [15:0] FRAME_W = WIDTH, FRAME_H = HEIGHT;
    localparam [15:0] JPEG_W = JPEG_WIDTH, JPEG_H = JPEG_HEIGHT;
    localparam integer PIXEL_ADDR_BITS = $clog2(WIDTH * HEIGHT);
    wire done_0, done_1, ack_0, ack_1;
    wire [31:0] sequence_0, sequence_1;
    wire [31:0] bytes_0, bytes_1;
    wire [7:0] data_0, data_1;
    reg [1:0] rearm_toggle, armed, rearm_released;
    (* ASYNC_REG = "TRUE" *) reg [1:0] done_sync_0, done_sync_1, ack_sync_0, ack_sync_1;
    wire [31:0] payload_index;
    wire [31:0] read_address = payload_index < 12 ? 0 : payload_index - 12;
    wire [PIXEL_ADDR_BITS-1:0] pixel_address = read_address >> 1;
    // Cameras are captured independently so one may run the RGB565 path that
    // feeds the tensor writer while the other streams sensor-native JPEG.
    generate if (CAM0_RGB) begin : cam0_rgb
        wire [15:0] pixel_0, read_data_0;
        wire valid_0, href_0, vsync_0;
        dvp_rgb565 decode_0 (
            .clk(camera_clk_0), .rst_n(rst_n), .configured(configured_0),
            .vsync(camera_vsync_0), .href(camera_href_0), .data(camera_data_0),
            .pixel_vsync(vsync_0), .pixel_href(href_0), .pixel_valid(valid_0), .pixel(pixel_0)
        );
        frame_capture #(.SOURCE_WIDTH(SOURCE_WIDTH), .SOURCE_HEIGHT(SOURCE_HEIGHT),
            .DOWNSAMPLE_BITS(DOWNSAMPLE_BITS), .PREVIEW_WIDTH(WIDTH), .PREVIEW_HEIGHT(HEIGHT),
            .AVERAGE_2X2(DOWNSAMPLE_BITS == 1), .PIXEL_BYTES(2),
            .RAM_BANK_ADDR_BITS(RGB_BUFFER_BANK_BITS)) frame_0 (
            .pixel_clk(camera_clk_0), .sys_clk(sys_clk), .rst_n(rst_n),
            .vsync(vsync_0), .href(href_0), .pixel_valid(valid_0), .pixel(pixel_0),
            .rearm_toggle(rearm_toggle[0]), .rearm_ack(ack_0), .read_addr(pixel_address),
            .read_data(read_data_0), .frame_done(done_0), .frame_sequence(sequence_0), .samples()
        );
        assign bytes_0 = WIDTH * HEIGHT * 2;
        assign data_0 = read_address[0] ? read_data_0[15:8] : read_data_0[7:0];
    end else begin : cam0_jpeg
        wire [ADDR_BITS:0] captured_bytes_0;
        assign bytes_0 = captured_bytes_0;
        jpeg_frame_capture #(.BUFFER_BYTES(BUFFER_BYTES)) frame_0 (
            .pixel_clk(camera_clk_0), .sys_clk(sys_clk), .rst_n(rst_n), .configured(configured_0),
            .vsync(camera_vsync_0), .href(camera_href_0), .data(camera_data_0),
            .rearm_toggle(rearm_toggle[0]), .rearm_ack(ack_0),
            .read_addr(read_address[ADDR_BITS-1:0]), .read_data(data_0), .frame_done(done_0),
            .frame_sequence(sequence_0), .frame_bytes(captured_bytes_0), .overflow_frames(), .incomplete_frames()
        );
    end endgenerate
    generate if (CAM1_RGB) begin : cam1_rgb
        wire [15:0] pixel_1, read_data_1;
        wire valid_1, href_1, vsync_1;
        dvp_rgb565 decode_1 (
            .clk(camera_clk_1), .rst_n(rst_n), .configured(configured_1),
            .vsync(camera_vsync_1), .href(camera_href_1), .data(camera_data_1),
            .pixel_vsync(vsync_1), .pixel_href(href_1), .pixel_valid(valid_1), .pixel(pixel_1)
        );
        frame_capture #(.SOURCE_WIDTH(SOURCE_WIDTH), .SOURCE_HEIGHT(SOURCE_HEIGHT),
            .DOWNSAMPLE_BITS(DOWNSAMPLE_BITS), .PREVIEW_WIDTH(WIDTH), .PREVIEW_HEIGHT(HEIGHT),
            .AVERAGE_2X2(DOWNSAMPLE_BITS == 1), .PIXEL_BYTES(2),
            .RAM_BANK_ADDR_BITS(RGB_BUFFER_BANK_BITS)) frame_1 (
            .pixel_clk(camera_clk_1), .sys_clk(sys_clk), .rst_n(rst_n),
            .vsync(vsync_1), .href(href_1), .pixel_valid(valid_1), .pixel(pixel_1),
            .rearm_toggle(rearm_toggle[1]), .rearm_ack(ack_1), .read_addr(pixel_address),
            .read_data(read_data_1), .frame_done(done_1), .frame_sequence(sequence_1), .samples()
        );
        assign bytes_1 = WIDTH * HEIGHT * 2;
        assign data_1 = read_address[0] ? read_data_1[15:8] : read_data_1[7:0];
    end else begin : cam1_jpeg
        wire [ADDR_BITS:0] captured_bytes_1;
        assign bytes_1 = captured_bytes_1;
        jpeg_frame_capture #(.BUFFER_BYTES(BUFFER_BYTES)) frame_1 (
            .pixel_clk(camera_clk_1), .sys_clk(sys_clk), .rst_n(rst_n), .configured(configured_1),
            .vsync(camera_vsync_1), .href(camera_href_1), .data(camera_data_1),
            .rearm_toggle(rearm_toggle[1]), .rearm_ack(ack_1),
            .read_addr(read_address[ADDR_BITS-1:0]), .read_data(data_1), .frame_done(done_1),
            .frame_sequence(sequence_1), .frame_bytes(captured_bytes_1), .overflow_frames(), .incomplete_frames()
        );
    end endgenerate
    assign frames_ready = {done_sync_1[1], done_sync_0[1]};
    reg [31:0] system_ticks, capture_ticks_0, capture_ticks_1;
    reg done_last_0, done_last_1;
    always @(posedge sys_clk or negedge rst_n) begin
        if (!rst_n) begin
            done_sync_0 <= 0; done_sync_1 <= 0; ack_sync_0 <= 0; ack_sync_1 <= 0;
            system_ticks <= 0; capture_ticks_0 <= 0; capture_ticks_1 <= 0;
            done_last_0 <= 0; done_last_1 <= 0;
        end else begin
            done_sync_0 <= {done_sync_0[0], done_0}; done_sync_1 <= {done_sync_1[0], done_1};
            ack_sync_0 <= {ack_sync_0[0], ack_0}; ack_sync_1 <= {ack_sync_1[0], ack_1};
            system_ticks <= system_ticks + 1'b1;
            done_last_0 <= done_sync_0[1]; done_last_1 <= done_sync_1[1];
            if (done_sync_0[1] && !done_last_0) capture_ticks_0 <= system_ticks;
            if (done_sync_1[1] && !done_last_1) capture_ticks_1 <= system_ticks;
        end
    end

    localparam OFF = 0, REARM = 1, RELEASE = 2, CAPTURE = 3, SNAPSHOT = 4,
        DIAG_ISSUE = 5, DIAG_WAIT = 6, IMAGE_ISSUE = 7, IMAGE_WAIT = 8, LAST_STOP = 9;
    reg [3:0] state;
    reg selected_camera, image_packet, skip_image, packet_start;
    reg [319:0] diagnostic_snapshot;
    reg [31:0] image_sequence, image_capture_ticks, send_ticks, image_bytes, capture_wait;
    reg [7:0] image_flags, payload_data;
    localparam integer CAPTURE_LEAD_TICKS = 64'd1 * CLOCK_HZ * CAPTURE_LEAD_US / 1000000;
    localparam integer MAX_SEND_AGE_TICKS = 64'd1 * CLOCK_HZ * MAX_SEND_AGE_US / 1000000;
    localparam integer UART_BYTE_TICKS = 10 * (CLOCK_HZ / BAUD_RATE) + 1;
    localparam integer CAPTURE_LEAD_BYTES = CAPTURE_LEAD_TICKS / UART_BYTE_TICKS;
    wire selected_done = selected_camera ? done_sync_1[1] : done_sync_0[1];
    wire selected_ack = selected_camera ? ack_sync_1[1] : ack_sync_0[1];
    wire selected_configured = selected_camera ? configured_1 : configured_0;
    wire [31:0] selected_sequence = selected_camera ? sequence_1 : sequence_0;
    wire [31:0] selected_ticks = selected_camera ? capture_ticks_1 : capture_ticks_0;
    wire [31:0] selected_bytes = selected_camera ? bytes_1 : bytes_0;
    wire [15:0] selected_bytes_low = selected_bytes > 65535 ? 16'hffff : selected_bytes[15:0];
    wire [127:0] selected_base = selected_camera ? diagnostic_base_1 : diagnostic_base_0;
    wire selected_rgb = selected_camera ? CAM1_RGB : CAM0_RGB;
    wire [15:0] selected_width = selected_rgb ? FRAME_W : JPEG_W;
    wire [15:0] selected_height = selected_rgb ? FRAME_H : JPEG_H;
    wire [7:0] packet_format = selected_rgb ? 8'd3 : 8'd5;
    wire packet_busy, packet_done;
    assign active = state != OFF;
    wire [319:0] diagnostic = {
        selected_base, selected_sequence[7:0], selected_sequence[15:8],
        selected_sequence[23:16], selected_sequence[31:24],
        selected_bytes_low[7:0], selected_bytes_low[15:8],
        {6'd0, selected_done, selected_configured}, selected_rgb ? 8'ha3 : 8'ha4,
        BUILD_ID[7:0], BUILD_ID[15:8], BUILD_ID[23:16], BUILD_ID[31:24],
        selected_width[7:0], selected_width[15:8], selected_height[7:0], selected_height[15:8],
        selected_bytes[7:0], selected_bytes[15:8], selected_bytes[23:16], selected_bytes[31:24],
        selected_bytes[7:0], selected_bytes[15:8], selected_bytes[23:16], selected_bytes[31:24]
    };
    always @* begin
        if (!image_packet) payload_data = diagnostic_snapshot[(39-payload_index[5:0])*8 +: 8];
        else case (payload_index)
            0: payload_data = selected_width[7:0];
            1: payload_data = selected_width[15:8];
            2: payload_data = selected_height[7:0];
            3: payload_data = selected_height[15:8];
            4: payload_data = image_capture_ticks[7:0];
            5: payload_data = image_capture_ticks[15:8];
            6: payload_data = image_capture_ticks[23:16];
            7: payload_data = image_capture_ticks[31:24];
            8: payload_data = send_ticks[7:0];
            9: payload_data = send_ticks[15:8];
            10: payload_data = send_ticks[23:16];
            11: payload_data = send_ticks[31:24];
            default: payload_data = selected_camera ? data_1 : data_0;
        endcase
    end
    camera_packet_tx packets (
        .clk(sys_clk), .rst_n(rst_n), .start(packet_start),
        .packet_type(image_packet ? 8'd1 : 8'd2), .camera({7'd0, selected_camera}),
        .format(packet_format), .flags(image_flags), .sequence_number(image_sequence),
        .payload_length(image_packet ? image_bytes+12 : 32'd40),
        .payload_data(payload_data), .payload_index(payload_index),
        .busy(packet_busy), .done(packet_done), .tx_data(tx_data), .tx_valid(tx_valid), .tx_ready(tx_ready)
    );
    always @(posedge sys_clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= OFF; selected_camera <= 0; rearm_toggle <= 0; armed <= 0; rearm_released <= 0;
            image_packet <= 0; skip_image <= 0; packet_start <= 0;
            diagnostic_snapshot <= 0; image_sequence <= 0; image_capture_ticks <= 0;
            send_ticks <= 0; image_bytes <= 0; capture_wait <= 0; image_flags <= 0;
        end else begin
            packet_start <= 0;
            // Ack and done cross independently: observe done low for this rearm
            // before treating a completed buffer as a newly captured frame.
            if (armed[0] && ack_sync_0[1] == rearm_toggle[0] && !done_sync_0[1])
                rearm_released[0] <= 1;
            if (armed[1] && ack_sync_1[1] == rearm_toggle[1] && !done_sync_1[1])
                rearm_released[1] <= 1;
            case (state)
                OFF: if (enable) state <= REARM;
                REARM: begin
                    if (!armed[selected_camera]) begin
                        rearm_toggle[selected_camera] <= ~rearm_toggle[selected_camera];
                        armed[selected_camera] <= 1;
                        rearm_released[selected_camera] <= 0;
                    end
                    capture_wait <= 0; state <= RELEASE;
                end
                RELEASE: begin
                    if (!enable) state <= LAST_STOP;
                    else if (selected_ack == rearm_toggle[selected_camera] && rearm_released[selected_camera])
                        state <= selected_done ? SNAPSHOT : CAPTURE;
                    else if (capture_wait == CLOCK_HZ/4-1) begin
                        armed[selected_camera] <= 0;
                        skip_image <= 1; image_flags <= {6'd0, selected_done, selected_configured};
                        state <= DIAG_ISSUE;
                    end else capture_wait <= capture_wait + 1'b1;
                end
                CAPTURE: begin
                    if (!enable) state <= LAST_STOP;
                    else if (selected_done) state <= SNAPSHOT;
                    else if (capture_wait == CLOCK_HZ/4-1) begin
                        armed[selected_camera] <= 0;
                        skip_image <= 1; image_flags <= {6'd0, selected_done, selected_configured};
                        state <= DIAG_ISSUE;
                    end else capture_wait <= capture_wait + 1'b1;
                end
                SNAPSHOT: begin
                    armed[selected_camera] <= 0;
                    if (OVERLAP_CAPTURE != 0 && system_ticks - selected_ticks > MAX_SEND_AGE_TICKS)
                        state <= REARM;
                    else begin
                        image_sequence <= selected_sequence; image_capture_ticks <= selected_ticks;
                        image_bytes <= selected_bytes; image_flags <= 8'h07; skip_image <= 0;
                        state <= DIAG_ISSUE;
                    end
                end
                DIAG_ISSUE: if (!packet_busy && tx_ready) begin
                    diagnostic_snapshot <= diagnostic; image_packet <= 0; packet_start <= 1; state <= DIAG_WAIT;
                end
                DIAG_WAIT: if (packet_done) state <= skip_image ? LAST_STOP : IMAGE_ISSUE;
                IMAGE_ISSUE: if (!packet_busy && tx_ready) begin
                    image_packet <= 1; send_ticks <= system_ticks; packet_start <= 1; state <= IMAGE_WAIT;
                end
                IMAGE_WAIT: begin
                    // Rearm only the other camera, whose buffer is no longer being transmitted.
                    if (OVERLAP_CAPTURE != 0 && enable && !armed[!selected_camera] &&
                        payload_index >= 12 && payload_index + CAPTURE_LEAD_BYTES >= image_bytes + 12) begin
                        rearm_toggle[!selected_camera] <= ~rearm_toggle[!selected_camera];
                        armed[!selected_camera] <= 1;
                        rearm_released[!selected_camera] <= 0;
                    end
                    if (packet_done) state <= LAST_STOP;
                end
                // UART ready returns only after the final checksum byte's stop bit.
                LAST_STOP: if (tx_ready) begin
                    selected_camera <= ~selected_camera;
                    if (!enable) begin armed <= 0; rearm_released <= 0; end
                    state <= enable ? REARM : OFF;
                end
                default: state <= OFF;
            endcase
        end
    end
endmodule
