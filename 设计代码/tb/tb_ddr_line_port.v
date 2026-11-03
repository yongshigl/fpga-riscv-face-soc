`timescale 1ns/1ps

module tb_ddr_line_port;
    reg clk = 0;
    reg rst_n = 0;
    always #4 clk = ~clk;
    wire bist_valid, bist_write, req_ready, rsp_valid, rsp_error;
    wire [29:0] bist_address;
    wire [255:0] bist_data, rsp_data;
    wire [31:0] bist_mask;
    wire done, failed;
    wire [31:0] errors, transactions;
    wire [29:0] first_address;
    reg direct_mode = 0, direct_valid = 0, respond = 1, allow_bus = 1, inject = 0;
    reg [29:0] direct_address = 0;
    wire [27:0] awaddr, araddr;
    wire awvalid, arvalid;
    wire [255:0] wdata;
    wire [31:0] wstrb;
    reg awready, arready, wready, rvalid;
    reg [255:0] rdata;
    reg write_pending, read_pending, injected;
    reg [5:0] write_index, read_index;
    reg [3:0] delay_count;
    reg [15:0] random_state;
    reg [255:0] memory [0:63];
    integer byte_number;

    function [5:0] memory_index;
        input [27:0] address;
        begin
            memory_index = {address[25] ? 2'd3 : address[22] ? 2'd2 : address[18] ? 2'd1 : 2'd0,
                            address[6:3]};
        end
    endfunction
    ddr_bist #(.INDEX_BITS(6)) bist (
        .clk(clk), .rst_n(rst_n), .initialized(1'b1), .req_valid(bist_valid),
        .req_ready(req_ready && !direct_mode), .req_write(bist_write),
        .req_byte_addr(bist_address), .req_wdata(bist_data), .req_wstrb(bist_mask),
        .rsp_valid(rsp_valid && !direct_mode), .rsp_rdata(rsp_data), .rsp_error(rsp_error),
        .done(done), .failed(failed), .errors(errors), .transactions(transactions),
        .first_error_address(first_address)
    );
    ddr_line_port #(.TIMEOUT_BITS(8)) port (
        .clk(clk), .rst_n(rst_n), .req_valid(direct_mode ? direct_valid : bist_valid),
        .req_ready(req_ready), .req_write(direct_mode ? 1'b0 : bist_write),
        .req_byte_addr(direct_mode ? direct_address : bist_address),
        .req_wdata(bist_data), .req_wstrb(bist_mask),
        .rsp_valid(rsp_valid), .rsp_ready(respond), .rsp_rdata(rsp_data), .rsp_error(rsp_error),
        .axi_awaddr(awaddr), .axi_awvalid(awvalid), .axi_awready(awready),
        .axi_wdata(wdata), .axi_wstrb(wstrb), .axi_wready(wready), .axi_wlast(1'b1),
        .axi_araddr(araddr), .axi_arvalid(arvalid), .axi_arready(arready),
        .axi_rdata(rdata), .axi_rvalid(rvalid), .axi_rlast(1'b1)
    );
    always @* begin
        awready = allow_bus && !write_pending && random_state[0];
        arready = allow_bus && !read_pending && random_state[1];
        wready = allow_bus && write_pending && random_state[2];
    end
    always @(posedge clk) begin
        if (!rst_n) begin
            random_state <= 16'hace1;
            write_pending <= 0;
            read_pending <= 0;
            rvalid <= 0;
            injected <= 0;
            delay_count <= 0;
        end else begin
            random_state <= {random_state[14:0], random_state[15] ^ random_state[13] ^ random_state[12] ^ random_state[10]};
            rvalid <= 0;
            if (awvalid && awready) begin
                if (awaddr[2:0] != 0) $fatal(1, "HMIC word-address alignment");
                write_pending <= 1;
                write_index <= memory_index(awaddr);
            end
            if (wready) begin
                for (byte_number = 0; byte_number < 32; byte_number = byte_number + 1)
                    if (wstrb[byte_number]) memory[write_index][byte_number*8 +: 8] <= wdata[byte_number*8 +: 8];
                write_pending <= 0;
            end
            if (arvalid && arready) begin
                read_pending <= 1;
                read_index <= memory_index(araddr);
                delay_count <= random_state[6:4] + 1'b1;
            end
            if (read_pending) begin
                if (delay_count != 0) delay_count <= delay_count - 1'b1;
                else begin
                    rvalid <= 1;
                    rdata <= memory[read_index];
                    read_pending <= 0;
                    if (inject && !injected && read_index == 3) begin
                        rdata <= memory[read_index] ^ 256'd1;
                        injected <= 1;
                    end
                end
            end
        end
    end
    task reset;
        begin
            @(negedge clk) rst_n = 0;
            repeat (3) @(negedge clk);
            rst_n = 1;
        end
    endtask
    reg [255:0] held_data;
    initial begin
        reset();
        wait(done);
        if (failed || errors != 0 || transactions != 384) $fatal(1, "BIST or byte-mask failure");
        $display("PASS: DDR line transfers, random stalls, four address regions and masked writes");
        inject = 1;
        reset();
        wait(done);
        if (!failed || errors != 1 || first_address != 96) $fatal(1, "Corruption was not localized");
        $display("PASS: DDR injected read corruption is detected and localized");
        direct_mode = 1;
        inject = 0;
        reset();
        @(negedge clk);
        direct_address = 0;
        direct_valid = 1;
        respond = 0;
        @(negedge clk) direct_valid = 0;
        wait(rsp_valid);
        held_data = rsp_data;
        repeat (7) begin
            @(negedge clk);
            if (!rsp_valid || rsp_error || rsp_data !== held_data || req_ready)
                $fatal(1, "Response changed under backpressure");
        end
        respond = 1;
        wait(req_ready);
        @(negedge clk);
        direct_address = 3;
        direct_valid = 1;
        @(negedge clk) direct_valid = 0;
        wait(rsp_valid);
        if (!rsp_error) $fatal(1, "Misaligned line request accepted");
        wait(req_ready);
        @(negedge clk);
        direct_address = 0;
        allow_bus = 0;
        direct_valid = 1;
        @(negedge clk) direct_valid = 0;
        wait(rsp_valid);
        if (!rsp_error) $fatal(1, "Timeout missing");
        repeat (10) @(negedge clk);
        if (req_ready) $fatal(1, "Timed-out port accepted an ambiguous new transaction");
        $display("PASS: DDR response backpressure, misalignment and timeout require-reset behavior");
        $finish;
    end
    initial begin
        #2000000;
        $fatal(1, "DDR test timeout");
    end
endmodule
