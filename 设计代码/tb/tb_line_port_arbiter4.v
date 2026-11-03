`timescale 1ns/1ps

module tb_line_port_arbiter4;
    reg clk = 0;
    always #4 clk = ~clk;
    reg rst_n = 0;

    reg [3:0] req_valid = 0;
    wire [3:0] req_ready;
    reg [3:0] req_write = 0;
    reg [127:0] req_addr = 0;
    reg [1023:0] req_wdata = 0;
    reg [127:0] req_wstrb = 0;
    wire [3:0] rsp_valid;
    reg [3:0] rsp_ready = 4'hf;
    wire [255:0] rsp_rdata;
    wire rsp_error;

    wire master_req_valid;
    reg master_req_ready = 0;
    wire master_req_write;
    wire [31:0] master_req_addr;
    wire [255:0] master_req_wdata;
    wire [31:0] master_req_wstrb;
    reg master_rsp_valid = 0;
    wire master_rsp_ready;
    reg [255:0] master_rsp_rdata = 0;
    reg master_rsp_error = 0;

    line_port_arbiter #(.CLIENTS(4)) dut (
        .clk(clk), .rst_n(rst_n),
        .req_valid(req_valid), .req_ready(req_ready), .req_write(req_write),
        .req_addr(req_addr), .req_wdata(req_wdata), .req_wstrb(req_wstrb),
        .rsp_valid(rsp_valid), .rsp_ready(rsp_ready), .rsp_rdata(rsp_rdata),
        .rsp_error(rsp_error),
        .master_req_valid(master_req_valid), .master_req_ready(master_req_ready),
        .master_req_write(master_req_write), .master_req_addr(master_req_addr),
        .master_req_wdata(master_req_wdata), .master_req_wstrb(master_req_wstrb),
        .master_rsp_valid(master_rsp_valid), .master_rsp_ready(master_rsp_ready),
        .master_rsp_rdata(master_rsp_rdata), .master_rsp_error(master_rsp_error)
    );

    reg [27:0] seq [0:3];
    reg [27:0] sent_seq [0:3];
    reg [3:0] waiting = 0;
    integer counts [0:3];
    integer errors = 0;
    integer accepted = 0;
    integer i;

    reg pending_rsp = 0;
    reg [1:0] pending_owner;
    reg [27:0] pending_seq;
    reg [1:0] response_delay = 0;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            req_valid <= 0; req_write <= 0; req_addr <= 0; req_wdata <= 0; req_wstrb <= 0;
            waiting <= 0;
            for (i = 0; i < 4; i = i + 1) begin
                seq[i] <= i * 1000;
                sent_seq[i] <= 0;
                counts[i] = 0;
            end
            accepted <= 0;
        end else begin
            master_req_ready <= (($random % 5) != 0);
            for (i = 0; i < 4; i = i + 1) begin
                if (!waiting[i] && !req_valid[i]) begin
                    req_valid[i] <= 1;
                    req_write[i] <= i[0];
                    req_addr[i*32 +: 32] <= {i[3:0], seq[i]};
                    req_wdata[i*256 +: 256] <= {32{seq[i][7:0]}};
                    req_wstrb[i*32 +: 32] <= 32'hffffffff;
                    sent_seq[i] <= seq[i];
                    seq[i] <= seq[i] + 1;
                end
                if (req_valid[i] && req_ready[i]) begin
                    req_valid[i] <= 0;
                    waiting[i] <= 1;
                end
                if (rsp_valid[i]) begin
                    if (rsp_rdata[31:0] != {i[3:0], sent_seq[i]}) begin
                        $display("ERROR owner=%0d expected=%08x got=%08x", i,
                                 {i[3:0], sent_seq[i]}, rsp_rdata[31:0]);
                        errors = errors + 1;
                    end
                    counts[i] = counts[i] + 1;
                    waiting[i] <= 0;
                end
            end
        end
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            master_rsp_valid <= 0; master_rsp_rdata <= 0; master_rsp_error <= 0;
            pending_rsp <= 0; response_delay <= 0; pending_owner <= 0; pending_seq <= 0;
        end else begin
            if (master_req_valid && master_req_ready && !pending_rsp) begin
                pending_rsp <= 1;
                pending_owner <= master_req_addr[31:28];
                pending_seq <= master_req_addr[27:0];
                response_delay <= ($random % 3);
                accepted <= accepted + 1;
            end else if (pending_rsp) begin
                if (response_delay != 0) response_delay <= response_delay - 1;
                else begin
                    master_rsp_valid <= 1;
                    master_rsp_rdata <= {224'd0, pending_owner, pending_seq};
                    master_rsp_error <= 0;
                    pending_rsp <= 0;
                end
            end
            if (master_rsp_valid && master_rsp_ready) master_rsp_valid <= 0;
        end
    end

    integer cycle;
    initial begin
        cycle = 0;
        repeat (5) @(negedge clk);
        rst_n = 1;
        while (accepted < 2000 && cycle < 200000) begin
            @(negedge clk);
            cycle = cycle + 1;
        end
        repeat (20) @(negedge clk);
        $display("ARB4 accepted=%0d counts=%0d/%0d/%0d/%0d errors=%0d cycles=%0d",
                 accepted, counts[0], counts[1], counts[2], counts[3], errors, cycle);
        if (errors == 0 && accepted >= 1999 && counts[0] > 400 && counts[1] > 400 &&
            counts[2] > 400 && counts[3] > 400)
            $display("ARB4 PASS");
        else
            $display("ARB4 FAIL");
        $finish;
    end
endmodule
