`timescale 1ns/1ps
`include "src/vision_config.vh"
`ifndef VISION_CAMERA_OVERLAP
`define VISION_CAMERA_OVERLAP 0
`define VISION_CAMERA_CAPTURE_LEAD_US 60000
`define VISION_CAMERA_MAX_SEND_AGE_US 30000
`endif
`ifndef VISION_AI_READ_CACHE_LINES
`define VISION_AI_READ_CACHE_LINES 2
`define VISION_AI_DEPTHWISE_CHANNEL_OUTER 0
`define VISION_AI_READ_CACHE_FULL_LRU 0
`define VISION_AI_DEPTHWISE_WINDOW_REUSE 0
`define VISION_AI_QUANT_CHANNEL_PIPELINE 0
`define VISION_AI_WRITE_QUEUE_DEPTH 32
`endif
`ifdef VISION_RGB_DATAPATH
`define VISION_RGB_MODE 1
`else
`define VISION_RGB_MODE 0
`endif
`ifdef VISION_RGB_DATAPATH
`ifdef VISION_CAM1_JPEG
`define VISION_CAM1_JPEG_MODE 1
`else
`define VISION_CAM1_JPEG_MODE 0
`endif
`else
`define VISION_CAM1_JPEG_MODE 0
`endif

module pg2l200h_mini_vision_top (
    input wire sys_clk, clk_p, clk_n, rst_in, uart_rx,
    output wire uart_tx,
    output wire [7:0] led,
`ifdef VISION_CAMERAS
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
`endif
    output wire mem_rst_n, mem_ck, mem_ck_n, mem_cke, mem_cs_n,
    output wire mem_ras_n, mem_cas_n, mem_we_n, mem_odt,
    output wire [14:0] mem_a,
    output wire [2:0] mem_ba,
    inout wire [3:0] mem_dqs, mem_dqs_n,
    inout wire [31:0] mem_dq,
    output wire [3:0] mem_dm
);
`ifdef VISION_CAMERAS
    localparam integer CAMERA_ENABLE = 1;
`else
    localparam integer CAMERA_ENABLE = 0;
`endif
    wire reference_clock, ui_clock, ddr_pll_lock, initialized, phy_fail;
    wire cpu_clock, ai_clock, cpu_pll_lock;
    reg [2:0] sys_reset, ui_reset, cpu_reset, ai_reset;
    (* ASYNC_REG = "TRUE" *) reg [1:0] cpu_initialized, ai_initialized;
    always @(posedge sys_clk or negedge rst_in) begin
        if (!rst_in) sys_reset <= 0;
        else sys_reset <= {sys_reset[1:0], 1'b1};
    end
    always @(posedge ui_clock or negedge rst_in or negedge ddr_pll_lock) begin
        if (!rst_in || !ddr_pll_lock) ui_reset <= 0;
        else ui_reset <= {ui_reset[1:0], 1'b1};
    end
    always @(posedge cpu_clock or negedge rst_in or negedge cpu_pll_lock) begin
        if (!rst_in || !cpu_pll_lock) begin cpu_reset <= 0; cpu_initialized <= 0; end
        else begin
            cpu_initialized <= {cpu_initialized[0], initialized};
            cpu_reset <= {cpu_reset[1:0], cpu_initialized[1]};
        end
    end
    always @(posedge ai_clock or negedge rst_in or negedge cpu_pll_lock) begin
        if (!rst_in || !cpu_pll_lock) begin ai_reset <= 0; ai_initialized <= 0; end
        else begin
            ai_initialized <= {ai_initialized[0], initialized};
            ai_reset <= {ai_reset[1:0], ai_initialized[1]};
        end
    end
    GTP_INBUFGDS #(.IOSTANDARD("DEFAULT"), .TERM_DIFF("ON")) reference_input (
        .O(reference_clock), .I(clk_p), .IB(clk_n)
    );
    GTP_GPLL #(
        .CLKIN_FREQ(27.0), .STATIC_RATIOI(1), .STATIC_RATIOM(2),
        .STATIC_RATIOF(`VISION_PLL_FEEDBACK),
        .STATIC_RATIO0(`VISION_CPU_DIVIDER), .STATIC_RATIO1(`VISION_AI_DIVIDER),
        .STATIC_RATIO2(15), .STATIC_RATIO3(15), .STATIC_RATIO4(15), .STATIC_RATIO5(15), .STATIC_RATIO6(15),
        .STATIC_DUTYF(`VISION_PLL_FEEDBACK),
        .STATIC_DUTY0(`VISION_CPU_DIVIDER), .STATIC_DUTY1(`VISION_AI_DIVIDER),
        .STATIC_DUTY2(15), .STATIC_DUTY3(15), .STATIC_DUTY4(15), .STATIC_DUTY5(15), .STATIC_DUTY6(15)
    ) clock_pll (
        .CLKIN1(sys_clk), .CLKIN2(1'b0), .CLKFB(1'b0), .CLKIN_SEL(1'b0),
        .CLKOUT0(cpu_clock), .CLKOUT1(ai_clock), .LOCK(cpu_pll_lock),
        .DPS_CLK(1'b0), .DPS_EN(1'b0), .DPS_DIR(1'b0),
        .CLKOUT0_SYN(1'b0), .CLKOUT1_SYN(1'b0), .CLKOUT2_SYN(1'b0),
        .CLKOUT3_SYN(1'b0), .CLKOUT4_SYN(1'b0), .CLKOUT5_SYN(1'b0), .CLKOUT6_SYN(1'b0), .CLKOUTF_SYN(1'b0),
        .PLL_PWD(1'b0), .RST(1'b0), .APB_CLK(1'b0), .APB_RST_N(1'b0),
        .APB_ADDR(5'd0), .APB_SEL(1'b0), .APB_EN(1'b0), .APB_WRITE(1'b0), .APB_WDATA(16'd0)
    );

    wire req_valid, req_ready, req_write, rsp_valid, rsp_ready, rsp_error;
    wire [31:0] req_address, req_mask;
    wire [255:0] req_data, rsp_data;
    wire [31:0] gpio, current_pc;
    wire trap_taken, accelerator_irq;
    wire [63:0] cycle_count, instret_count;
    wire [7:0] accelerator_state;
    wire cpu_uart_tx;
    wire camera_req_valid, camera_req_ready, camera_req_write;
    wire camera_rsp_valid, camera_rsp_ready, camera_rsp_error;
    wire [31:0] camera_req_addr, camera_req_wdata, camera_rsp_rdata;
    wire [3:0] camera_req_wstrb;
    vision_soc #(.CPU_CLOCK_HZ(`VISION_CPU_HZ), .AI_CLOCK_HZ(`VISION_AI_HZ),
                 .AI_READ_CACHE_LINES(`VISION_AI_READ_CACHE_LINES),
                 .AI_DEPTHWISE_CHANNEL_OUTER(`VISION_AI_DEPTHWISE_CHANNEL_OUTER),
                 .AI_READ_CACHE_FULL_LRU(`VISION_AI_READ_CACHE_FULL_LRU),
                 .ICACHE_SET_BITS(`VISION_ICACHE_SET_BITS),
                 .DCACHE_SET_BITS(`VISION_DCACHE_SET_BITS),
                 .MAC_PIXELS(`VISION_MAC_PIXELS), .CAMERA_ENABLE(CAMERA_ENABLE)) system (
        .cpu_clk(cpu_clock), .cpu_rst_n(cpu_reset[2]), .ai_clk(ai_clock), .ai_rst_n(ai_reset[2]),
        .memory_clk(ui_clock), .memory_rst_n(ui_reset[2] && initialized),
        .uart_rx(uart_rx), .uart_tx(cpu_uart_tx), .gpio(gpio), .current_pc(current_pc), .trap_taken(trap_taken),
        .cycle_count(cycle_count), .instret_count(instret_count),
        .accelerator_state(accelerator_state), .accelerator_irq(accelerator_irq),
        .mem_req_valid(req_valid), .mem_req_ready(req_ready), .mem_req_write(req_write),
        .mem_req_addr(req_address), .mem_req_wdata(req_data), .mem_req_wstrb(req_mask),
        .mem_rsp_valid(rsp_valid), .mem_rsp_ready(rsp_ready), .mem_rsp_rdata(rsp_data), .mem_rsp_error(rsp_error),
        .camera_req_valid(camera_req_valid), .camera_req_ready(camera_req_ready),
        .camera_req_write(camera_req_write), .camera_req_addr(camera_req_addr),
        .camera_req_wdata(camera_req_wdata), .camera_req_wstrb(camera_req_wstrb),
        .camera_rsp_valid(camera_rsp_valid), .camera_rsp_ready(camera_rsp_ready),
        .camera_rsp_rdata(camera_rsp_rdata), .camera_rsp_error(camera_rsp_error)
    );

`ifdef VISION_RGB_DATAPATH
    wire [15:0] tensor_pixel;
    wire tensor_pixel_vsync, tensor_pixel_href, tensor_pixel_valid;
    wire tw_req_valid, tw_req_ready, tw_req_write;
    wire [31:0] tw_req_addr, tw_req_mask;
    wire [255:0] tw_req_data;
    wire tw_rsp_valid, tw_rsp_ready, tw_rsp_error;
    wire [31:0] tensor_sequence;
    wire tensor_done, tensor_overflow;
    wire [31:0] tensor_samples, tensor_lines, tensor_groups;
    wire fw_req_valid, fw_req_ready, fw_req_write;
    wire [31:0] fw_req_addr, fw_req_mask;
    wire [255:0] fw_req_data;
    wire fw_rsp_valid, fw_rsp_ready, fw_rsp_error;
    wire [31:0] frame_sequence, frame_pixels;
    wire frame_done, frame_last_bank;
    wire [31:0] frame_dropped;
    wire ar_req_valid, ar_req_ready, ar_req_write;
    wire [31:0] ar_req_addr, ar_req_mask;
    wire [255:0] ar_req_data;
    wire ar_rsp_valid, ar_rsp_ready, ar_rsp_error;
    wire affine_busy, affine_done_toggle;
    wire [31:0] affine_completed;
    wire signed [31:0] affine_a, affine_b, affine_c, affine_d, affine_e, affine_f;
    wire [31:0] affine_src_base;
    wire affine_start_toggle;
    wire frame_lock_valid, frame_lock_bank;
    wire capture_request_toggle, capture_acknowledge_toggle, capture_enable;
    wire [3:0] ddr_req_ready_2, ddr_rsp_valid_2;
    reg tensor_done_toggle, frame_done_toggle;
    always @(posedge ui_clock or negedge ui_reset[2]) begin
        if (!ui_reset[2]) begin
            tensor_done_toggle <= 0;
            frame_done_toggle <= 0;
        end else begin
            if (tensor_done) tensor_done_toggle <= ~tensor_done_toggle;
            if (frame_done) frame_done_toggle <= ~frame_done_toggle;
        end
    end
`endif

    wire ddr_req_valid, ddr_req_ready, ddr_req_write;
    wire [31:0] ddr_req_addr, ddr_req_mask;
    wire [255:0] ddr_req_data, ddr_rsp_data, arb_rsp_data;
    wire ddr_rsp_valid, ddr_rsp_ready, ddr_rsp_error;

`ifdef VISION_CAMERAS
    wire preview_tx, preview_active;
    wire camera_io_req_valid, camera_io_req_ready, camera_io_req_write;
    wire camera_io_rsp_valid, camera_io_rsp_ready, camera_io_rsp_error;
    wire [31:0] camera_io_req_addr, camera_io_req_wdata, camera_io_rsp_rdata;
    wire [3:0] camera_io_req_wstrb;
    wire [1:0] cameras_configured, camera_frames_ready;
    transaction_cdc #(.DATA_BITS(32)) camera_crossing (
        .source_clk(cpu_clock), .source_rst_n(cpu_reset[2]),
        .source_req_valid(camera_req_valid), .source_req_ready(camera_req_ready),
        .source_req_write(camera_req_write), .source_req_addr(camera_req_addr),
        .source_req_wdata(camera_req_wdata), .source_req_wstrb(camera_req_wstrb),
        .source_rsp_valid(camera_rsp_valid), .source_rsp_ready(camera_rsp_ready),
        .source_rsp_rdata(camera_rsp_rdata), .source_rsp_error(camera_rsp_error),
        .target_clk(sys_clk), .target_rst_n(sys_reset[2]),
        .target_req_valid(camera_io_req_valid), .target_req_ready(camera_io_req_ready),
        .target_req_write(camera_io_req_write), .target_req_addr(camera_io_req_addr),
        .target_req_wdata(camera_io_req_wdata), .target_req_wstrb(camera_io_req_wstrb),
        .target_rsp_valid(camera_io_rsp_valid), .target_rsp_ready(camera_io_rsp_ready),
        .target_rsp_rdata(camera_io_rsp_rdata), .target_rsp_error(camera_io_rsp_error)
    );
`ifdef VISION_RGB_DATAPATH
    dvp_rgb565 tensor_decode (
        .clk(camera_clk_0), .rst_n(sys_reset[2]), .configured(cameras_configured[0]),
        .vsync(camera_vsync_0), .href(camera_href_0), .data(camera_data_0),
        .pixel_vsync(tensor_pixel_vsync), .pixel_href(tensor_pixel_href),
        .pixel_valid(tensor_pixel_valid), .pixel(tensor_pixel)
    );
    camera_capture_gate capture_gate (
        .pixel_clk(camera_clk_0), .rst_n(sys_reset[2]),
        .configured(cameras_configured[0]), .vsync(tensor_pixel_vsync),
        .request_toggle(capture_request_toggle), .acknowledge_toggle(capture_acknowledge_toggle),
        .tensor_done_toggle(tensor_done_toggle), .frame_done_toggle(frame_done_toggle),
        .capture_enable(capture_enable)
    );
    camera_tensor_writer #(.CAPTURE_CONTROL(1)) tensor_writer (
        .pixel_clk(camera_clk_0), .pixel_rst_n(sys_reset[2]),
        .configured(cameras_configured[0]), .capture_enable(capture_enable),
        .vsync(tensor_pixel_vsync), .href(tensor_pixel_href),
        .pixel_valid(tensor_pixel_valid), .pixel(tensor_pixel),
        .mem_clk(ui_clock), .mem_rst_n(ui_reset[2] && initialized),
        .mem_req_valid(tw_req_valid), .mem_req_ready(tw_req_ready),
        .mem_req_write(tw_req_write), .mem_req_addr(tw_req_addr),
        .mem_req_wdata(tw_req_data), .mem_req_wstrb(tw_req_mask),
        .mem_rsp_valid(tw_rsp_valid), .mem_rsp_ready(tw_rsp_ready),
        .mem_rsp_rdata(256'd0), .mem_rsp_error(1'b0),
        .frame_done(tensor_done), .frame_sequence(tensor_sequence), .overflow(tensor_overflow),
        .debug_samples(tensor_samples), .debug_lines(tensor_lines), .debug_groups(tensor_groups)
    );
    camera_frame_writer #(.CAPTURE_CONTROL(1)) frame_writer (
        .pixel_clk(camera_clk_0), .pixel_rst_n(sys_reset[2]),
        .configured(cameras_configured[0]), .capture_enable(capture_enable),
        .vsync(tensor_pixel_vsync), .href(tensor_pixel_href),
        .pixel_valid(tensor_pixel_valid), .pixel(tensor_pixel),
        .frame_lock_valid(frame_lock_valid), .frame_lock_bank(frame_lock_bank),
        .mem_clk(ui_clock), .mem_rst_n(ui_reset[2] && initialized),
        .mem_req_valid(fw_req_valid), .mem_req_ready(fw_req_ready),
        .mem_req_write(fw_req_write), .mem_req_addr(fw_req_addr),
        .mem_req_wdata(fw_req_data), .mem_req_wstrb(fw_req_mask),
        .mem_rsp_valid(fw_rsp_valid), .mem_rsp_ready(fw_rsp_ready),
        .mem_rsp_rdata(256'd0), .mem_rsp_error(ddr_rsp_error),
        .frame_done(frame_done), .frame_sequence(frame_sequence),
        .last_bank(frame_last_bank), .completed_frames(),
        .dropped_lines(frame_dropped), .frame_pixels(frame_pixels)
    );
    camera_affine_resampler affine_engine (
        .clk(ui_clock), .rst_n(ui_reset[2] && initialized),
        .start_toggle(affine_start_toggle),
        .coord_a(affine_a), .coord_b(affine_b), .coord_c(affine_c),
        .coord_d(affine_d), .coord_e(affine_e), .coord_f(affine_f),
        .source_base(affine_src_base),
        .busy(affine_busy), .done_toggle(affine_done_toggle), .completed(affine_completed),
        .mem_req_valid(ar_req_valid), .mem_req_ready(ar_req_ready),
        .mem_req_write(ar_req_write), .mem_req_addr(ar_req_addr),
        .mem_req_wdata(ar_req_data), .mem_req_wstrb(ar_req_mask),
        .mem_rsp_valid(ar_rsp_valid), .mem_rsp_ready(ar_rsp_ready),
        .mem_rsp_rdata(ddr_rsp_data), .mem_rsp_error(ddr_rsp_error)
    );
    line_port_arbiter #(.CLIENTS(4)) ddr_arbiter (
        .clk(ui_clock), .rst_n(ui_reset[2] && initialized),
        .req_valid({ar_req_valid, fw_req_valid, tw_req_valid, req_valid}),
        .req_ready(ddr_req_ready_2),
        .req_write({ar_req_write, fw_req_write, tw_req_write, req_write}),
        .req_addr({ar_req_addr, fw_req_addr, tw_req_addr, req_address}),
        .req_wdata({ar_req_data, fw_req_data, tw_req_data, req_data}),
        .req_wstrb({ar_req_mask, fw_req_mask, tw_req_mask, req_mask}),
        .rsp_valid(ddr_rsp_valid_2),
        .rsp_ready({ar_rsp_ready, fw_rsp_ready, tw_rsp_ready, rsp_ready}),
        .rsp_rdata(arb_rsp_data), .rsp_error(ddr_rsp_error),
        .master_req_valid(ddr_req_valid), .master_req_ready(ddr_req_ready),
        .master_req_write(ddr_req_write), .master_req_addr(ddr_req_addr),
        .master_req_wdata(ddr_req_data), .master_req_wstrb(ddr_req_mask),
        .master_rsp_valid(ddr_rsp_valid), .master_rsp_ready(ddr_rsp_ready),
        .master_rsp_rdata(ddr_rsp_data), .master_rsp_error(ddr_rsp_error)
    );
    assign req_ready = ddr_req_ready_2[0];
    assign rsp_valid = ddr_rsp_valid_2[0];
    assign rsp_data = ddr_rsp_data;
    assign rsp_error = ddr_rsp_error;
    assign tw_req_ready = ddr_req_ready_2[1];
    assign tw_rsp_valid = ddr_rsp_valid_2[1];
    assign tw_rsp_error = ddr_rsp_error;
    assign fw_req_ready = ddr_req_ready_2[2];
    assign fw_rsp_valid = ddr_rsp_valid_2[2];
    assign fw_rsp_error = ddr_rsp_error;
    assign ar_req_ready = ddr_req_ready_2[3];
    assign ar_rsp_valid = ddr_rsp_valid_2[3];
    assign ar_rsp_error = ddr_rsp_error;
`else
    assign ddr_req_valid = req_valid;
    assign req_ready = ddr_req_ready;
    assign ddr_req_write = req_write;
    assign ddr_req_addr = req_address;
    assign ddr_req_data = req_data;
    assign ddr_req_mask = req_mask;
    assign rsp_valid = ddr_rsp_valid;
    assign ddr_rsp_ready = rsp_ready;
    assign rsp_data = ddr_rsp_data;
    assign rsp_error = ddr_rsp_error;
`endif

    camera_control_port #(.JPEG_QUALITY(`VISION_JPEG_QUALITY), .RGB_MODE(`VISION_RGB_MODE),
        .CAM1_JPEG(`VISION_CAM1_JPEG_MODE),
        .OVERLAP_CAPTURE(`VISION_CAMERA_OVERLAP),
        .RGB_PREVIEW_DOWNSAMPLE_BITS(3),
        .CAPTURE_LEAD_US(`VISION_CAMERA_CAPTURE_LEAD_US),
        .MAX_SEND_AGE_US(`VISION_CAMERA_MAX_SEND_AGE_US)) cameras (
        .clk(sys_clk), .rst_n(sys_reset[2]),
        .req_valid(camera_io_req_valid), .req_ready(camera_io_req_ready),
        .req_write(camera_io_req_write), .req_addr(camera_io_req_addr),
        .req_wdata(camera_io_req_wdata), .req_wstrb(camera_io_req_wstrb),
        .rsp_valid(camera_io_rsp_valid), .rsp_ready(camera_io_rsp_ready),
        .rsp_rdata(camera_io_rsp_rdata), .rsp_error(camera_io_rsp_error),
        .camera_clk_0(camera_clk_0), .camera_href_0(camera_href_0), .camera_vsync_0(camera_vsync_0),
        .camera_data_0(camera_data_0), .SCL_0(SCL_0), .SDA_0(SDA_0), .cam_rst_0(cam_rst_0),
        .camera_clk_1(camera_clk_1), .camera_href_1(camera_href_1), .camera_vsync_1(camera_vsync_1),
        .camera_data_1(camera_data_1), .SCL_1(SCL_1), .SDA_1(SDA_1), .cam_rst_1(cam_rst_1),
        .preview_tx(preview_tx), .preview_active(preview_active),
        .configured(cameras_configured), .frames_ready(camera_frames_ready),
`ifdef VISION_RGB_DATAPATH
        .tensor_sequence(tensor_sequence), .tensor_done_toggle(tensor_done_toggle),
        .tensor_overflow(tensor_overflow),
        .tensor_samples(tensor_samples), .tensor_lines(tensor_lines), .tensor_groups(tensor_groups),
        .frame_sequence(frame_sequence), .frame_last_bank(frame_last_bank),
        .affine_busy(affine_busy), .affine_done_toggle(affine_done_toggle),
        .affine_completed(affine_completed),
        .capture_request_toggle(capture_request_toggle), .capture_acknowledge_toggle(capture_acknowledge_toggle),
        .affine_a(affine_a), .affine_b(affine_b), .affine_c(affine_c),
        .affine_d(affine_d), .affine_e(affine_e), .affine_f(affine_f),
        .affine_src_base(affine_src_base), .affine_start_toggle(affine_start_toggle),
        .frame_lock_valid(frame_lock_valid), .frame_lock_bank(frame_lock_bank)
`else
        .tensor_sequence(32'd0), .tensor_done_toggle(1'b0), .tensor_overflow(1'b0),
        .tensor_samples(32'd0), .tensor_lines(32'd0), .tensor_groups(32'd0),
        .frame_sequence(32'd0), .frame_last_bank(1'b0),
        .affine_busy(1'b0), .affine_done_toggle(1'b0), .affine_completed(32'd0),
        .capture_request_toggle(), .capture_acknowledge_toggle(1'b0),
        .affine_a(), .affine_b(), .affine_c(), .affine_d(), .affine_e(), .affine_f(),
        .affine_src_base(), .affine_start_toggle(),
        .frame_lock_valid(), .frame_lock_bank()
`endif
    );
    assign uart_tx = preview_active ? preview_tx : cpu_uart_tx;
`else
    assign camera_req_ready = 0;
    assign camera_rsp_valid = 0;
    assign camera_rsp_rdata = 0;
    assign camera_rsp_error = 0;
    assign uart_tx = cpu_uart_tx;
`endif

    wire [27:0] awaddr, araddr;
    wire [3:0] awlen, arlen;
    wire awvalid, awready, wready, wlast, arvalid, arready, rvalid, rlast;
    wire [255:0] wdata, rdata;
    wire [31:0] wstrb;
    ddr_line_port port (
        .clk(ui_clock), .rst_n(ui_reset[2] && initialized),
        .req_valid(ddr_req_valid), .req_ready(ddr_req_ready), .req_write(ddr_req_write),
        .req_byte_addr(ddr_req_addr[29:0]), .req_wdata(ddr_req_data), .req_wstrb(ddr_req_mask),
        .rsp_valid(ddr_rsp_valid), .rsp_ready(ddr_rsp_ready), .rsp_rdata(ddr_rsp_data), .rsp_error(ddr_rsp_error),
        .axi_awaddr(awaddr), .axi_awlen(awlen), .axi_awvalid(awvalid), .axi_awready(awready),
        .axi_wdata(wdata), .axi_wstrb(wstrb), .axi_wready(wready), .axi_wlast(wlast),
        .axi_araddr(araddr), .axi_arlen(arlen), .axi_arvalid(arvalid), .axi_arready(arready),
        .axi_rdata(rdata), .axi_rvalid(rvalid), .axi_rlast(rlast)
    );
    DDR_200H memory_controller (
        .ref_clk(reference_clock), .resetn(sys_reset[2]), .core_clk(ui_clock), .pll_lock(ddr_pll_lock),
        .phy_pll_lock(), .gpll_lock(), .rst_gpll_lock(), .ddrphy_cpd_lock(), .ddr_init_done(initialized),
        .axi_awaddr(awaddr), .axi_awuser_ap(1'b0), .axi_awuser_id(4'd0),
        .axi_awlen(awlen), .axi_awready(awready), .axi_awvalid(awvalid),
        .axi_wdata(wdata), .axi_wstrb(wstrb), .axi_wready(wready), .axi_wusero_last(wlast),
        .axi_araddr(araddr), .axi_aruser_ap(1'b0), .axi_aruser_id(4'd0),
        .axi_arlen(arlen), .axi_arready(arready), .axi_arvalid(arvalid),
        .axi_rdata(rdata), .axi_rvalid(rvalid), .axi_rlast(rlast),
        .apb_clk(1'b0), .apb_rst_n(1'b1), .apb_sel(1'b0), .apb_enable(1'b0),
        .apb_addr(8'd0), .apb_write(1'b0), .apb_wdata(16'd0),
        .mem_rst_n(mem_rst_n), .mem_ck(mem_ck), .mem_ck_n(mem_ck_n),
        .mem_cke(mem_cke), .mem_cs_n(mem_cs_n), .mem_ras_n(mem_ras_n), .mem_cas_n(mem_cas_n),
        .mem_we_n(mem_we_n), .mem_odt(mem_odt), .mem_a(mem_a), .mem_ba(mem_ba),
        .mem_dqs(mem_dqs), .mem_dqs_n(mem_dqs_n), .mem_dq(mem_dq), .mem_dm(mem_dm),
        .dbg_gate_start(1'b0), .dbg_cpd_start(1'b0), .dbg_ddrphy_rst_n(1'b1), .dbg_gpll_scan_rst(1'b0),
        .samp_position_dyn_adj(1'b0), .init_samp_position_even(32'd0), .init_samp_position_odd(32'd0),
        .wrcal_position_dyn_adj(1'b0), .init_wrcal_position(32'd0),
        .force_read_clk_ctrl(1'b0), .init_slip_step(16'd0), .init_read_clk_ctrl(12'd0),
        .dbg_ddrphy_init_fail(phy_fail), .debug_cpd_offset_adj(1'b0), .debug_cpd_offset_dir(1'b0),
        .debug_cpd_offset(10'd0), .ck_dly_en(1'b0), .init_ck_dly_step(8'd0)
    );
    assign led = ~{cpu_reset[2], ai_reset[2], gpio[6], trap_taken, phy_fail,
                   accelerator_irq, initialized, cycle_count[24]};
endmodule
