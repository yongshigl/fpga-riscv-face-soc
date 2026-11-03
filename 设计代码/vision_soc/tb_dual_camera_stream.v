`timescale 1ns/1ps

// Split-mode check: camera 0 delivers RGB565 pixels through the DVP path while
// camera 1 delivers a sensor-native JPEG byte stream. The two cameras share the
// stream FSM, so the packets must alternate and carry per-camera geometry.
module tb_dual_camera_stream #(
    parameter integer RGB_BUFFER_BANK_BITS = 5,
    parameter integer DOWNSAMPLE_BITS = 1,
    parameter integer JPEG_WIDTH = 640,
    parameter integer JPEG_HEIGHT = 360
);
    reg sys_clk = 0, pixel_clk_0 = 0, pixel_clk_1 = 0, rst_n = 0, enable = 0;
    always #18.5185 sys_clk = ~sys_clk;
    always #5 pixel_clk_0 = ~pixel_clk_0;
    always #6 pixel_clk_1 = ~pixel_clk_1;
    reg vsync_0 = 1, href_0 = 0, vsync_1 = 1, href_1 = 0;
    reg [7:0] data_0 = 0, data_1 = 0;
    wire active, tx_valid, tx_ready, tx;
    wire [7:0] tx_data;
    wire [1:0] frames_ready;
    integer row_0, col_0, i_1, frames = 0, bit_number, output_file;
    reg [15:0] value_0;
    reg [7:0] received;
    reg [7:0] jpeg_1 [0:65535];
    integer length_1;
    reg [1023:0] path_1, output_path;
    wire [127:0] diagnostic_rgb = {8'd6, 16'h5640, 8'h61, 8'd1, 8'd0, 8'd2,
        16'd64, 16'd32, 16'd0, 16'h300b, 8'd244};
    wire [127:0] diagnostic_jpeg = {8'd6, 16'h5640, 8'h30, 8'd0, 8'd0, 8'd2,
        16'd640, 16'd360, 16'd0, 16'h4407, 8'd244};
    camera_native_stream #(.RGB_MODE(1), .CAM1_JPEG(1), .SOURCE_WIDTH(64), .SOURCE_HEIGHT(32),
        .DOWNSAMPLE_BITS(DOWNSAMPLE_BITS), .BUFFER_BYTES(65536),
        .JPEG_WIDTH(JPEG_WIDTH), .JPEG_HEIGHT(JPEG_HEIGHT),
        .RGB_BUFFER_BANK_BITS(RGB_BUFFER_BANK_BITS),
        .CAPTURE_LEAD_US(50), .MAX_SEND_AGE_US(100)) dut (
        .sys_clk(sys_clk), .rst_n(rst_n), .enable(enable), .active(active),
        .configured_0(1'b1), .configured_1(1'b1),
        .diagnostic_base_0(diagnostic_rgb), .diagnostic_base_1(diagnostic_jpeg),
        .camera_clk_0(pixel_clk_0), .camera_href_0(href_0), .camera_vsync_0(vsync_0), .camera_data_0(data_0),
        .camera_clk_1(pixel_clk_1), .camera_href_1(href_1), .camera_vsync_1(vsync_1), .camera_data_1(data_1),
        .tx_data(tx_data), .tx_valid(tx_valid), .tx_ready(tx_ready), .frames_ready(frames_ready)
    );
    localparam integer UART_CLOCKS = 27000000/921600;
    uart_tx #(.CLOCK_HZ(27000000), .BAUD_RATE(921600)) serial_tx (
        .clk(sys_clk), .rst_n(rst_n), .data(tx_data), .valid(tx_valid),
        .ready(tx_ready), .tx(tx), .busy()
    );
    initial begin
        if (!$value$plusargs("jpeg1=%s", path_1) || !$value$plusargs("length1=%d", length_1) ||
            !$value$plusargs("output=%s", output_path)) $fatal(1, "Missing fixture paths");
        if (length_1 > 65536) $fatal(1, "JPEG fixture does not fit");
        $readmemh(path_1, jpeg_1, 0, length_1-1);
        output_file = $fopen(output_path, "wb");
        if (!output_file) $fatal(1, "Could not open output");
        repeat (5) @(negedge sys_clk);
        rst_n = 1; enable = 1;
    end
    // Camera 0: real DVP RGB565 pairs.
    initial begin
        wait(rst_n);
        forever begin
            @(negedge pixel_clk_0); vsync_0 = 1; href_0 = 0;
            repeat (8) @(negedge pixel_clk_0);
            vsync_0 = 0;
            repeat (8) @(negedge pixel_clk_0);
            for (row_0 = 0; row_0 < 32; row_0 = row_0 + 1) begin
                href_0 = 1;
                for (col_0 = 0; col_0 < 64; col_0 = col_0 + 1) begin
                    value_0 = (3 << 11) | (((col_0*3+row_0*5) & 63) << 5) | ((col_0+row_0) & 31);
                    data_0 = value_0[15:8]; @(negedge pixel_clk_0);
                    data_0 = value_0[7:0]; @(negedge pixel_clk_0);
                end
                href_0 = 0;
                repeat (8) @(negedge pixel_clk_0);
            end
        end
    end
    // Camera 1: JPEG bytes with spaced-out href gaps.
    initial begin
        wait(rst_n);
        forever begin
            @(negedge pixel_clk_1); vsync_1 = 1; href_1 = 0;
            repeat (11) @(negedge pixel_clk_1);
            vsync_1 = 0;
            repeat (5) @(negedge pixel_clk_1);
            for (i_1 = 0; i_1 < length_1; i_1 = i_1 + 1) begin
                href_1 = 1; data_1 = jpeg_1[i_1]; @(negedge pixel_clk_1);
                if (i_1 % 71 == 0) begin href_1 = 0; repeat (3) @(negedge pixel_clk_1); end
            end
            href_1 = 0; repeat (333) @(negedge pixel_clk_1);
        end
    end
    // Decode the actual UART wire.
    initial begin
        wait(rst_n);
        forever begin
            @(negedge tx);
            repeat (UART_CLOCKS/2) @(posedge sys_clk); #1;
            if (tx !== 0) $fatal(1, "Invalid start bit");
            for (bit_number = 0; bit_number < 8; bit_number = bit_number + 1) begin
                repeat (UART_CLOCKS) @(posedge sys_clk); #1;
                received[bit_number] = tx;
            end
            repeat (UART_CLOCKS) @(posedge sys_clk); #1;
            if (tx !== 1) $fatal(1, "Invalid stop bit");
            $fwrite(output_file, "%c", received);
        end
    end
    reg finish_requested = 0;
    always @(posedge sys_clk) begin
        if (dut.packet_done && dut.image_packet) begin
            if (dut.selected_camera != (frames & 1)) $fatal(1, "Camera order changed");
            if (frames >= 3) enable = 0;  // leaves the FSM in LAST_STOP, no partial packet
            else frames = frames + 1;
        end
    end
    initial begin
        wait(rst_n);
        wait(frames == 3 && dut.packet_done && dut.image_packet);
        wait(!active);
        repeat (4000) @(negedge sys_clk);  // last byte still on the wire
        $fclose(output_file);
        $display("PASS split camera stream: rgb cam0, jpeg cam1, %0d image packets", frames + 1);
        $finish;
    end
    initial begin #100000000; $fatal(1, "Split stream timeout"); end
endmodule
