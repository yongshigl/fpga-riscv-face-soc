`timescale 1ns/1ps

// Lock ownership through the response; rotate priority after each request.
module line_port_arbiter #(
    parameter integer CLIENTS = 2,
    parameter integer OWNER_BITS = $clog2(CLIENTS)
) (
    input wire clk, rst_n,
    input wire [CLIENTS-1:0] req_valid,
    output reg [CLIENTS-1:0] req_ready,
    input wire [CLIENTS-1:0] req_write,
    input wire [CLIENTS*32-1:0] req_addr,
    input wire [CLIENTS*256-1:0] req_wdata,
    input wire [CLIENTS*32-1:0] req_wstrb,
    output reg [CLIENTS-1:0] rsp_valid,
    input wire [CLIENTS-1:0] rsp_ready,
    output wire [255:0] rsp_rdata,
    output wire rsp_error,
    output wire master_req_valid,
    input wire master_req_ready,
    output wire master_req_write,
    output wire [31:0] master_req_addr,
    output wire [255:0] master_req_wdata,
    output wire [31:0] master_req_wstrb,
    input wire master_rsp_valid,
    output wire master_rsp_ready,
    input wire [255:0] master_rsp_rdata,
    input wire master_rsp_error
);
    reg busy;
    reg [OWNER_BITS-1:0] owner, next_owner;
    reg [OWNER_BITS-1:0] chosen;
    reg found;
    integer index, candidate;
    always @* begin
        chosen = next_owner;
        found = 0;
        for (index = 0; index < CLIENTS; index = index + 1) begin
            candidate = (next_owner + index) % CLIENTS;
            if (!found && req_valid[candidate]) begin
                chosen = candidate;
                found = 1;
            end
        end
        req_ready = 0;
        rsp_valid = 0;
        if (!busy && found) req_ready[chosen] = master_req_ready;
        if (busy) rsp_valid[owner] = master_rsp_valid;
    end
    assign master_req_valid = !busy && found;
    assign master_req_write = req_write[chosen];
    assign master_req_addr = req_addr[chosen*32 +: 32];
    assign master_req_wdata = req_wdata[chosen*256 +: 256];
    assign master_req_wstrb = req_wstrb[chosen*32 +: 32];
    assign master_rsp_ready = busy && rsp_ready[owner];
    assign rsp_rdata = master_rsp_rdata;
    assign rsp_error = master_rsp_error;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            busy <= 0;
            owner <= 0;
            next_owner <= 0;
        end else begin
            if (master_req_valid && master_req_ready) begin
                owner <= chosen;
                next_owner <= chosen == CLIENTS-1 ? 0 : chosen + 1'b1;
                busy <= 1;
            end
            if (master_rsp_valid && master_rsp_ready) busy <= 0;
        end
    end
endmodule
