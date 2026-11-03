`timescale 1ns/1ps

module tb_early_data_port;
    reg clk = 0, rst_n = 0;
    always #5 clk = !clk;
    reg cpu_valid = 0, cpu_write = 0, early_valid = 0;
    reg [31:0] cpu_addr = 0, cpu_wdata = 0, early_addr = 0;
    reg [3:0] cpu_wstrb = 0;
    reg req_ready = 0, rsp_valid = 0, rsp_error = 0;
    reg [31:0] rsp_rdata = 0;
    wire cpu_ready, cpu_error, req_valid, req_write, rsp_ready;
    wire [31:0] cpu_rdata, req_addr, req_wdata;
    wire [3:0] req_wstrb;
    integer requests = 0, responses = 0, writes = 0;
    reg [31:0] last_address;

    cpu_data_port #(.EARLY_LOAD_REQUEST(1)) dut (
        .clk(clk), .rst_n(rst_n), .cpu_valid(cpu_valid), .cpu_write(cpu_write),
        .cpu_addr(cpu_addr), .cpu_wdata(cpu_wdata), .cpu_wstrb(cpu_wstrb),
        .early_valid(early_valid), .early_addr(early_addr),
        .cpu_ready(cpu_ready), .cpu_error(cpu_error), .cpu_rdata(cpu_rdata),
        .req_valid(req_valid), .req_ready(req_ready), .req_write(req_write),
        .req_addr(req_addr), .req_wdata(req_wdata), .req_wstrb(req_wstrb),
        .rsp_valid(rsp_valid), .rsp_ready(rsp_ready), .rsp_error(rsp_error), .rsp_rdata(rsp_rdata)
    );

    always @(posedge clk) if (rst_n) begin
        if (req_valid && req_ready) begin
            requests <= requests + 1;
            last_address <= req_addr;
            if (req_write) writes <= writes + 1;
        end
        if (rsp_valid && rsp_ready) responses <= responses + 1;
    end

    task tick;
        begin @(posedge clk); #1; end
    endtask

    initial begin
        tick;
        rst_n = 1;
        // The response arrives before the consumer has entered MEM.
        early_valid = 1; early_addr = 32'h30001004; req_ready = 1;
        #1;
        if (!req_valid || req_write || req_addr !== early_addr || req_wstrb != 0)
            $fatal(1, "Early load request fields differ");
        tick;
        early_valid = 0; req_ready = 0; rsp_valid = 1; rsp_error = 1; rsp_rdata = 32'h12345678;
        repeat (3) begin
            #1;
            if (rsp_ready || cpu_ready || req_valid) $fatal(1, "Early response consumed without MEM");
            tick;
        end
        cpu_valid = 1; cpu_addr = 32'h30001004;
        #1;
        if (!cpu_ready || !cpu_error || cpu_rdata !== 32'h12345678)
            $fatal(1, "Early fault/data not delivered to MEM");
        tick;
        cpu_valid = 0; rsp_valid = 0; rsp_error = 0;
        if (requests != 1 || responses != 1) $fatal(1, "Duplicate early transaction");

        // A denied EX offer becomes the same held MEM request.
        early_valid = 1; early_addr = 32'h10000004;
        repeat (3) begin
            #1;
            if (!req_valid || req_addr !== 32'h10000004) $fatal(1, "Lost denied EX offer");
            tick;
        end
        early_valid = 0; early_addr = 32'h100000fc;
        cpu_valid = 1; cpu_addr = 32'h10000004;
        repeat (2) begin
            #1;
            if (!req_valid || req_addr !== 32'h10000004) $fatal(1, "MEM fallback fields changed");
            tick;
        end
        req_ready = 1;
        tick;
        req_ready = 0;
        repeat (3) begin
            #1;
            if (req_valid || cpu_ready) $fatal(1, "Held MEM generated a duplicate request");
            tick;
        end
        rsp_valid = 1; rsp_rdata = 32'h89abcdef;
        #1;
        if (!cpu_ready || cpu_rdata !== 32'h89abcdef) $fatal(1, "Fallback load data differs");
        tick;
        cpu_valid = 0; rsp_valid = 0;
        if (requests != 2 || responses != 2 || last_address !== 32'h10000004)
            $fatal(1, "Fallback transaction count differs");

        cpu_valid = 1; cpu_write = 1; cpu_addr = 32'h10000008;
        cpu_wdata = 32'h55aa33cc; cpu_wstrb = 4'b1010;
        early_valid = 1; early_addr = 32'h1000000c; req_ready = 1;
        #1;
        if (!req_valid || !req_write || req_addr !== cpu_addr ||
            req_wdata !== cpu_wdata || req_wstrb !== cpu_wstrb)
            $fatal(1, "Older MEM store did not take priority");
        tick;
        repeat (3) begin
            #1;
            if (req_valid) $fatal(1, "Younger early load bypassed pending MEM store");
            tick;
        end
        rsp_valid = 1;
        tick;
        cpu_valid = 0; cpu_write = 0; early_valid = 0; req_ready = 0; rsp_valid = 0;
        if (requests != 3 || responses != 3 || writes != 1 || last_address !== 32'h10000008)
            $fatal(1, "Store priority/count differs");

        early_valid = 1; early_addr = 32'h10000010; req_ready = 1;
        tick;
        req_ready = 0; early_valid = 0; rst_n = 0;
        tick;
        rst_n = 1;
        #1;
        if (req_valid || rsp_ready || cpu_ready) $fatal(1, "Reset retained a pending early load");
        cpu_valid = 1; cpu_addr = 32'h10000014; req_ready = 1;
        tick;
        req_ready = 0; rsp_valid = 1; rsp_rdata = 32'h76543210;
        #1;
        if (!cpu_ready || cpu_rdata !== 32'h76543210) $fatal(1, "Post-reset load differs");
        tick;
        cpu_valid = 0; rsp_valid = 0;
        if (requests != 5 || responses != 4 || writes != 1)
            $fatal(1, "Post-reset transaction count differs");
        $display("PASS: early response/fault held for MEM, denied-request fallback, backpressure, older-store priority, single transactions and reset");
        $finish;
    end
    initial begin
        #5000;
        $fatal(1, "Early data-port test timed out");
    end
endmodule
