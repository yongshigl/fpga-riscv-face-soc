`timescale 1ns/1ps

module line_port_mux2 (
    input wire selected,
    input wire [63:0] req_addr,
    input wire [511:0] req_wdata,
    input wire [63:0] req_wstrb,
    input wire [1:0] req_write,
    output reg [31:0] master_addr,
    output reg [255:0] master_wdata,
    output reg [31:0] master_wstrb,
    output reg master_write
);
    always @* begin
        if (!selected) begin
            master_addr = req_addr[31:0];
            master_wdata = req_wdata[255:0];
            master_wstrb = req_wstrb[31:0];
            master_write = req_write[0];
        end else begin
            master_addr = req_addr[63:32];
            master_wdata = req_wdata[511:256];
            master_wstrb = req_wstrb[63:32];
            master_write = req_write[1];
        end
    end
endmodule

module line_port_mux4 (
    input wire [1:0] selected,
    input wire [127:0] req_addr,
    input wire [1023:0] req_wdata,
    input wire [127:0] req_wstrb,
    input wire [3:0] req_write,
    output reg [31:0] master_addr,
    output reg [255:0] master_wdata,
    output reg [31:0] master_wstrb,
    output reg master_write
);
    always @* begin
        case (selected)
            2'd0: begin
                master_addr = req_addr[31:0];
                master_wdata = req_wdata[255:0];
                master_wstrb = req_wstrb[31:0];
                master_write = req_write[0];
            end
            2'd1: begin
                master_addr = req_addr[63:32];
                master_wdata = req_wdata[511:256];
                master_wstrb = req_wstrb[63:32];
                master_write = req_write[1];
            end
            2'd2: begin
                master_addr = req_addr[95:64];
                master_wdata = req_wdata[767:512];
                master_wstrb = req_wstrb[95:64];
                master_write = req_write[2];
            end
            default: begin
                master_addr = req_addr[127:96];
                master_wdata = req_wdata[1023:768];
                master_wstrb = req_wstrb[127:96];
                master_write = req_write[3];
            end
        endcase
    end
endmodule

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
    reg out_valid, out_write;
    reg [31:0] out_addr, out_wstrb;
    reg [255:0] out_wdata;
    wire [31:0] selected_addr;
    wire [255:0] selected_wdata;
    wire [31:0] selected_wstrb;
    wire selected_write;
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
        if (!busy && found) req_ready[chosen] = 1'b1;
        if (busy) rsp_valid[owner] = master_rsp_valid;
    end
    generate if (CLIENTS == 2) begin : mux_2
        line_port_mux2 selected_bus (
            .selected(chosen[0]), .req_addr(req_addr), .req_wdata(req_wdata),
            .req_wstrb(req_wstrb), .req_write(req_write),
            .master_addr(selected_addr), .master_wdata(selected_wdata),
            .master_wstrb(selected_wstrb), .master_write(selected_write)
        );
    end else begin : mux_4
        line_port_mux4 selected_bus (
            .selected(chosen), .req_addr(req_addr), .req_wdata(req_wdata),
            .req_wstrb(req_wstrb), .req_write(req_write),
            .master_addr(selected_addr), .master_wdata(selected_wdata),
            .master_wstrb(selected_wstrb), .master_write(selected_write)
        );
    end endgenerate
    assign master_req_valid = out_valid;
    assign master_req_write = out_write;
    assign master_req_addr = out_addr;
    assign master_req_wdata = out_wdata;
    assign master_req_wstrb = out_wstrb;
    assign master_rsp_ready = busy && rsp_ready[owner];
    assign rsp_rdata = master_rsp_rdata;
    assign rsp_error = master_rsp_error;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            busy <= 0;
            owner <= 0;
            next_owner <= 0;
            out_valid <= 0; out_write <= 0; out_addr <= 0; out_wdata <= 0; out_wstrb <= 0;
        end else begin
            if (!busy && found) begin
                owner <= chosen;
                next_owner <= chosen == CLIENTS-1 ? 0 : chosen + 1'b1;
                busy <= 1;
                out_valid <= 1;
                out_write <= selected_write;
                out_addr <= selected_addr;
                out_wdata <= selected_wdata;
                out_wstrb <= selected_wstrb;
            end
            if (out_valid && master_req_ready) out_valid <= 0;
            if (master_rsp_valid && master_rsp_ready) busy <= 0;
        end
    end
endmodule
