`timescale 1ns/1ps

module ov5640_control #(
    parameter integer CLOCK_HZ = 27_000_000,
    parameter integer NATIVE_JPEG = 0,
    parameter integer JPEG_WIDTH = 640,
    parameter integer JPEG_HEIGHT = 360,
    parameter integer JPEG_QUALITY = 8
) (
    input  wire        clk,
    input  wire        rst_n,
    output wire       scl,
    inout  wire       sda,
    output reg        camera_rst_n,
    output reg        init_done,
    output reg  [7:0]  status_code,
    output reg  [15:0] chip_id,
    output reg  [7:0]  format,
    output reg  [7:0]  isp_format,
    output reg  [7:0]  test_pattern,
    output reg  [7:0]  system_control,
    output reg  [15:0] width,
    output reg  [15:0] height,
    output reg  [15:0] nack_count,
    output reg  [15:0] last_address,
    output reg  [7:0]  write_count
);
    localparam [3:0] RESET_HOLD = 0, RESET_WAIT = 1,
        ID_START = 2, ID_WAIT = 3, CFG_REQUEST = 4, CFG_WAIT = 5,
        SOFT_RESET_WAIT = 6, VERIFY_START = 7, VERIFY_WAIT = 8,
        VERIFY_CHECK = 9, COMPLETE = 10, FAILED = 11,
        JPEG_REQUEST = 12, JPEG_WAIT = 13, JPEG_SETTLE = 14;
    reg [3:0] state;
    reg [23:0] delay_count;
    reg [3:0] read_index;
    reg [4:0] jpeg_index;
    reg jpeg_verify_pending;
    reg [7:0] jpeg_mode, jpeg_enable, jpeg_quantization;
    reg config_enable;
    wire table_start;
    wire [23:0] table_data;
    reg table_advance;
    reg master_start;
    reg master_read;
    reg [15:0] master_address;
    reg [7:0] master_data;
    wire [7:0] master_result;
    wire master_done, master_nack;

    data_init register_table (
        .sys_clk(clk), .sys_rst_n(rst_n && config_enable),
        .cfg_end(table_advance), .cfg_start(table_start),
        .cfg_data(table_data), .cfg_done()
    );

    sccb_master #(.CLOCK_HZ(CLOCK_HZ)) master (
        .clk(clk), .rst_n(rst_n), .start(master_start),
        .read(master_read), .address(master_address),
        .write_data(master_data), .read_data(master_result),
        .busy(), .done(master_done), .nack(master_nack),
        .scl(scl), .sda(sda)
    );

    function [15:0] verify_address;
        input [3:0] index;
        begin
            case (index)
                0: verify_address = 16'h4300;
                1: verify_address = 16'h501f;
                2: verify_address = 16'h503d;
                3: verify_address = 16'h3808;
                4: verify_address = 16'h3809;
                5: verify_address = 16'h380a;
                6: verify_address = 16'h380b;
                7: verify_address = 16'h3008;
                8: verify_address = 16'h300a;
                9: verify_address = 16'h300b;
                10: verify_address = 16'h4713;
                11: verify_address = 16'h3821;
                default: verify_address = 16'h4407;
            endcase
        end
    endfunction

    function [23:0] jpeg_register;
        input [4:0] index;
        begin
            case (index)
                // Format changes use the running SCCB clock, as in the OV5640 driver.
                0: jpeg_register = 24'h501f00;
                1: jpeg_register = 24'h430030;
                2: jpeg_register = 24'h300200;
                3: jpeg_register = 24'h3006ff;
                4: jpeg_register = 24'h471c50;
                5: jpeg_register = 24'h382121;
                6: jpeg_register = 24'h471303;
                7: jpeg_register = {16'h4407, JPEG_QUALITY[7:0]};
                8: jpeg_register = 24'h460b35;
                9: jpeg_register = 24'h460c22;
                10: jpeg_register = 24'h382402;
                11: jpeg_register = {16'h3808, JPEG_WIDTH[15:8]};
                12: jpeg_register = {16'h3809, JPEG_WIDTH[7:0]};
                13: jpeg_register = {16'h380a, JPEG_HEIGHT[15:8]};
                14: jpeg_register = {16'h380b, JPEG_HEIGHT[7:0]};
                default: jpeg_register = 24'h5001a3;
            endcase
        end
    endfunction
    wire [23:0] jpeg_write = jpeg_register(jpeg_index);

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= RESET_HOLD;
            delay_count <= 0;
            read_index <= 0;
            jpeg_index <= 0;
            jpeg_verify_pending <= 0;
            jpeg_mode <= 0; jpeg_enable <= 0; jpeg_quantization <= 0;
            config_enable <= 0;
            table_advance <= 0;
            master_start <= 0;
            master_read <= 0;
            master_address <= 0;
            master_data <= 0;
            camera_rst_n <= 0;
            init_done <= 0;
            status_code <= 0;
            chip_id <= 0;
            format <= 0;
            isp_format <= 0;
            test_pattern <= 0;
            system_control <= 0;
            width <= 0;
            height <= 0;
            nack_count <= 0;
            last_address <= 0;
            write_count <= 0;
        end else begin
            table_advance <= 0;
            master_start <= 0;
            case (state)
                RESET_HOLD: begin
                    status_code <= 1;
                    if (delay_count == CLOCK_HZ/100-1) begin
                        camera_rst_n <= 1;
                        delay_count <= 0;
                        state <= RESET_WAIT;
                    end else delay_count <= delay_count + 1'b1;
                end
                RESET_WAIT: begin
                    status_code <= 2;
                    if (delay_count == CLOCK_HZ/25-1) begin
                        delay_count <= 0;
                        read_index <= 0;
                        state <= ID_START;
                    end else delay_count <= delay_count + 1'b1;
                end
                ID_START: begin
                    status_code <= 3;
                    master_read <= 1;
                    master_address <= read_index == 0 ? 16'h300a : 16'h300b;
                    last_address <= read_index == 0 ? 16'h300a : 16'h300b;
                    master_start <= 1;
                    state <= ID_WAIT;
                end
                ID_WAIT: if (master_done) begin
                    if (master_nack) begin
                        nack_count <= nack_count + 1'b1;
                        status_code <= 8'he1;
                        state <= FAILED;
                    end else if (read_index == 0) begin
                        chip_id[15:8] <= master_result;
                        read_index <= 1;
                        state <= ID_START;
                    end else begin
                        chip_id[7:0] <= master_result;
                        if (chip_id[15:8] == 8'h56 && master_result == 8'h40) begin
                            config_enable <= 1;
                            status_code <= 4;
                            state <= CFG_REQUEST;
                        end else begin
                            status_code <= 8'he2;
                            state <= FAILED;
                        end
                    end
                end
                CFG_REQUEST: if (table_start && write_count < 244) begin
                    master_read <= 0;
                    master_address <= table_data[23:8];
                    last_address <= table_data[23:8];
                    master_data <= table_data[7:0];
                    master_start <= 1;
                    state <= CFG_WAIT;
                end
                CFG_WAIT: if (master_done) begin
                    if (master_nack) begin
                        nack_count <= nack_count + 1'b1;
                        status_code <= 8'he3;
                        state <= FAILED;
                    end else begin
                        write_count <= write_count + 1'b1;
                        if (write_count == 243) begin
                            // The vendor table ends with four writes to read-only ID registers.
                            config_enable <= 0;
                            read_index <= 0;
                            delay_count <= 0;
                            state <= NATIVE_JPEG ? JPEG_SETTLE : VERIFY_START;
                        end else if (master_address == 16'h3008 && master_data[7]) begin
                            delay_count <= 0;
                            state <= SOFT_RESET_WAIT;
                        end else begin
                            table_advance <= 1;
                            state <= CFG_REQUEST;
                        end
                    end
                end
                SOFT_RESET_WAIT: begin
                    if (delay_count == CLOCK_HZ/100-1) begin
                        table_advance <= 1;
                        state <= CFG_REQUEST;
                    end else delay_count <= delay_count + 1'b1;
                end
                JPEG_REQUEST: begin
                    master_read <= 0;
                    master_address <= jpeg_write[23:8];
                    last_address <= jpeg_write[23:8];
                    master_data <= jpeg_write[7:0];
                    master_start <= 1;
                    state <= JPEG_WAIT;
                end
                JPEG_WAIT: if (master_done) begin
                    if (master_nack) begin
                        nack_count <= nack_count + 1'b1;
                        status_code <= 8'he6;
                        state <= FAILED;
                    end else if (jpeg_index == 15) begin
                        read_index <= 0;
                        delay_count <= 0;
                        jpeg_verify_pending <= 1;
                        state <= JPEG_SETTLE;
                    end else begin
                        jpeg_index <= jpeg_index + 1'b1;
                        state <= JPEG_REQUEST;
                    end
                end
                JPEG_SETTLE: begin
                    if (delay_count == CLOCK_HZ/10-1) begin
                        delay_count <= 0;
                        state <= jpeg_verify_pending ? VERIFY_START : JPEG_REQUEST;
                    end else delay_count <= delay_count + 1'b1;
                end
                VERIFY_START: begin
                    status_code <= 5;
                    master_read <= 1;
                    master_address <= verify_address(read_index);
                    last_address <= verify_address(read_index);
                    master_start <= 1;
                    state <= VERIFY_WAIT;
                end
                VERIFY_WAIT: if (master_done) begin
                    if (master_nack) begin
                        nack_count <= nack_count + 1'b1;
                        status_code <= 8'he4;
                        state <= FAILED;
                    end else begin
                        case (read_index)
                            0: format <= master_result;
                            1: isp_format <= master_result;
                            2: test_pattern <= master_result;
                            3: width[15:8] <= master_result;
                            4: width[7:0] <= master_result;
                            5: height[15:8] <= master_result;
                            6: height[7:0] <= master_result;
                            7: system_control <= master_result;
                            8: chip_id[15:8] <= master_result;
                            9: chip_id[7:0] <= master_result;
                            10: jpeg_mode <= master_result;
                            11: jpeg_enable <= master_result;
                            12: jpeg_quantization <= master_result;
                        endcase
                        read_index <= read_index + 1'b1;
                        state <= read_index == (NATIVE_JPEG ? 12 : 9) ? VERIFY_CHECK : VERIFY_START;
                    end
                end
                VERIFY_CHECK: begin
                    if (chip_id == 16'h5640 && test_pattern == 0 && !system_control[6] &&
                        (NATIVE_JPEG ? (format == 8'h30 && isp_format == 0 &&
                            width == JPEG_WIDTH && height == JPEG_HEIGHT &&
                            jpeg_mode[1:0] == 3 && jpeg_enable[5] && jpeg_quantization == JPEG_QUALITY) :
                            (format == 8'h61 && isp_format == 8'h01 && width == 1280 && height == 720))) begin
                        init_done <= 1;
                        status_code <= 8'h06;
                        state <= COMPLETE;
                    end else begin
                        status_code <= 8'he5;
                        state <= FAILED;
                    end
                end
                COMPLETE: begin end
                FAILED: begin
                    init_done <= 0;
                    config_enable <= 0;
                end
                default: state <= RESET_HOLD;
            endcase
        end
    end
endmodule
