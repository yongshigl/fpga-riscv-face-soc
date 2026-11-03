`timescale 1ns/1ps

// One outstanding 32-byte transaction on the Pango HMIC interface.
// HMIC addresses are 32-bit words; its write-data strobe is the transfer event.
module ddr_line_port #(
    parameter integer TIMEOUT_BITS = 20
) (
    input wire clk,
    input wire rst_n,
    input wire req_valid,
    output wire req_ready,
    input wire req_write,
    input wire [29:0] req_byte_addr,
    input wire [255:0] req_wdata,
    input wire [31:0] req_wstrb,
    output reg rsp_valid,
    input wire rsp_ready,
    output reg [255:0] rsp_rdata,
    output reg rsp_error,
    output wire [27:0] axi_awaddr,
    output wire [3:0] axi_awlen,
    output wire axi_awvalid,
    input wire axi_awready,
    output wire [255:0] axi_wdata,
    output wire [31:0] axi_wstrb,
    input wire axi_wready,
    input wire axi_wlast,
    output wire [27:0] axi_araddr,
    output wire [3:0] axi_arlen,
    output wire axi_arvalid,
    input wire axi_arready,
    input wire [255:0] axi_rdata,
    input wire axi_rvalid,
    input wire axi_rlast
);
    localparam IDLE = 0, WRITE_ADDR = 1, WRITE_DATA = 2,
               READ_ADDR = 3, READ_DATA = 4, RESPONSE = 5, FAULT = 6;
    reg [2:0] state;
    reg [27:0] address;
    reg [255:0] write_data;
    reg [31:0] write_mask;
    reg [TIMEOUT_BITS-1:0] timeout_count;

    assign req_ready = state == IDLE;
    assign axi_awaddr = address;
    assign axi_araddr = address;
    assign axi_awlen = 0;
    assign axi_arlen = 0;
    assign axi_awvalid = state == WRITE_ADDR;
    assign axi_arvalid = state == READ_ADDR;
    assign axi_wdata = write_data;
    assign axi_wstrb = write_mask;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= IDLE;
            address <= 0;
            write_data <= 0;
            write_mask <= 0;
            timeout_count <= 0;
            rsp_valid <= 0;
            rsp_error <= 0;
            rsp_rdata <= 0;
        end else begin
            if (state == IDLE || state == RESPONSE || state == FAULT)
                timeout_count <= 0;
            else
                timeout_count <= timeout_count + 1'b1;

            case (state)
                IDLE: if (req_valid) begin
                    address <= {req_byte_addr[29:5], 3'b0};
                    write_data <= req_wdata;
                    write_mask <= req_wstrb;
                    rsp_error <= req_byte_addr[4:0] != 0;
                    rsp_rdata <= 0;
                    if (req_byte_addr[4:0] != 0) begin
                        rsp_valid <= 1;
                        state <= RESPONSE;
                    end else
                        state <= req_write ? WRITE_ADDR : READ_ADDR;
                end
                WRITE_ADDR: if (axi_awready) begin
                    if (axi_wready) begin
                        rsp_error <= !axi_wlast;
                        rsp_valid <= 1;
                        state <= RESPONSE;
                    end else
                        state <= WRITE_DATA;
                end
                WRITE_DATA: if (axi_wready) begin
                    rsp_error <= !axi_wlast;
                    rsp_valid <= 1;
                    state <= RESPONSE;
                end
                READ_ADDR: if (axi_arready) begin
                    if (axi_rvalid) begin
                        rsp_rdata <= axi_rdata;
                        rsp_error <= !axi_rlast;
                        rsp_valid <= 1;
                        state <= RESPONSE;
                    end else
                        state <= READ_DATA;
                end
                READ_DATA: if (axi_rvalid) begin
                    rsp_rdata <= axi_rdata;
                    rsp_error <= !axi_rlast;
                    rsp_valid <= 1;
                    state <= RESPONSE;
                end
                RESPONSE: if (rsp_ready) begin
                    rsp_valid <= 0;
                    state <= IDLE;
                end
                // After a timeout the controller may still return stale data.
                // Require reset instead of accepting another ambiguous transfer.
                FAULT: if (rsp_ready)
                    rsp_valid <= 0;
                default: state <= FAULT;
            endcase
            if (state != IDLE && state != RESPONSE && state != FAULT && &timeout_count) begin
                rsp_valid <= 1;
                rsp_error <= 1;
                rsp_rdata <= 0;
                state <= FAULT;
            end
        end
    end
endmodule
