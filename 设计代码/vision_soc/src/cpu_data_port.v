`timescale 1ns/1ps

// Convert the held MEM-stage request to one request/response transaction.
module cpu_data_port #(
    parameter integer EARLY_LOAD_REQUEST =
`ifdef CPU_EARLY_LOAD_REQUEST
        `CPU_EARLY_LOAD_REQUEST
`else
        0
`endif
) (
    input wire clk, rst_n,
    input wire cpu_valid, cpu_write,
    input wire [31:0] cpu_addr, cpu_wdata,
    input wire [3:0] cpu_wstrb,
    input wire early_valid,
    input wire [31:0] early_addr,
    output wire cpu_ready, cpu_error,
    output wire [31:0] cpu_rdata,
    output wire req_valid,
    input wire req_ready,
    output wire req_write,
    output wire [31:0] req_addr, req_wdata,
    output wire [3:0] req_wstrb,
    input wire rsp_valid,
    output wire rsp_ready,
    input wire rsp_error,
    input wire [31:0] rsp_rdata
);
    reg pending;
    wire select_early = EARLY_LOAD_REQUEST != 0 && !cpu_valid;
    assign req_valid = (cpu_valid || (EARLY_LOAD_REQUEST != 0 && early_valid)) && !pending;
    assign req_write = select_early ? 1'b0 : cpu_write;
    assign req_addr = select_early ? early_addr : cpu_addr;
    assign req_wdata = select_early ? 32'd0 : cpu_wdata;
    assign req_wstrb = select_early ? 4'd0 : cpu_wstrb;
    // An early response must wait until its instruction is present in MEM.
    assign rsp_ready = pending && (EARLY_LOAD_REQUEST == 0 || cpu_valid);
    assign cpu_ready = rsp_ready && rsp_valid;
    assign cpu_error = rsp_error;
    assign cpu_rdata = rsp_rdata;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) pending <= 0;
        else begin
            if (req_valid && req_ready) pending <= 1;
            if (rsp_valid && rsp_ready) pending <= 0;
        end
    end
endmodule
