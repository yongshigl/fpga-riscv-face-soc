`timescale 1ns/1ps

// One outstanding transaction. Payloads stay stable until their synchronized
// request/response toggles are acknowledged. Either reset cancels both sides.
module transaction_cdc #(
    parameter integer DATA_BITS = 256,
    parameter integer MASK_BITS = DATA_BITS / 8,
    parameter integer RESPONSE_CAPTURE_BYPASS = 0,
    parameter integer RESPONSE_REQUEST_TURNOVER = 0,
    parameter integer REQUEST_CAPTURE_BYPASS = 0
) (
    input wire source_clk, source_rst_n,
    input wire source_req_valid,
    output wire source_req_ready,
    input wire source_req_write,
    input wire [31:0] source_req_addr,
    input wire [DATA_BITS-1:0] source_req_wdata,
    input wire [MASK_BITS-1:0] source_req_wstrb,
    output wire source_rsp_valid,
    input wire source_rsp_ready,
    output wire [DATA_BITS-1:0] source_rsp_rdata,
    output wire source_rsp_error,
    input wire target_clk, target_rst_n,
    output wire target_req_valid,
    input wire target_req_ready,
    output wire target_req_write,
    output wire [31:0] target_req_addr,
    output wire [DATA_BITS-1:0] target_req_wdata,
    output wire [MASK_BITS-1:0] target_req_wstrb,
    input wire target_rsp_valid,
    output wire target_rsp_ready,
    input wire [DATA_BITS-1:0] target_rsp_rdata,
    input wire target_rsp_error
);
    wire reset_n = source_rst_n && target_rst_n;
    (* ASYNC_REG = "TRUE" *) reg [1:0] source_reset, target_reset;
    (* ASYNC_REG = "TRUE" *) reg [1:0] request_sync, response_sync;
    reg request_toggle, response_toggle, request_seen, response_seen;
    reg [1:0] source_state, target_state;
    reg held_write;
    reg [31:0] held_addr;
    reg [DATA_BITS-1:0] held_wdata, held_response;
    reg [MASK_BITS-1:0] held_wstrb;
    reg held_error;
    reg target_write;
    reg [31:0] target_addr;
    reg [DATA_BITS-1:0] target_wdata, source_response;
    reg [MASK_BITS-1:0] target_wstrb;
    reg source_error;
    wire response_pending = source_state == 1 && response_sync[1] != response_seen;
    wire early_response = RESPONSE_CAPTURE_BYPASS != 0 && response_pending;
    wire response_complete = source_rsp_valid && source_rsp_ready;
    wire request_pending = target_state == 0 && request_sync[1] != request_seen;
    wire early_request = REQUEST_CAPTURE_BYPASS != 0 && request_pending;

    always @(posedge source_clk or negedge reset_n) begin
        if (!reset_n) begin source_reset <= 0; response_sync <= 0; end
        else begin source_reset <= {source_reset[0], 1'b1}; response_sync <= {response_sync[0], response_toggle}; end
    end
    always @(posedge target_clk or negedge reset_n) begin
        if (!reset_n) begin target_reset <= 0; request_sync <= 0; end
        else begin target_reset <= {target_reset[0], 1'b1}; request_sync <= {request_sync[0], request_toggle}; end
    end
    assign source_req_ready = source_reset[1] &&
        (source_state == 0 || (RESPONSE_REQUEST_TURNOVER != 0 &&
         response_complete && !source_rsp_error));
    assign source_rsp_valid = source_reset[1] && (source_state == 2 || early_response);
    assign source_rsp_rdata = early_response ? held_response : source_response;
    assign source_rsp_error = early_response ? held_error : source_error;
    assign target_req_valid = target_reset[1] && (target_state == 1 || early_request);
    assign target_req_addr = REQUEST_CAPTURE_BYPASS != 0 ? held_addr : target_addr;
    assign target_req_write = REQUEST_CAPTURE_BYPASS != 0 ? held_write : target_write;
    assign target_req_wdata = REQUEST_CAPTURE_BYPASS != 0 ? held_wdata : target_wdata;
    assign target_req_wstrb = REQUEST_CAPTURE_BYPASS != 0 ? held_wstrb : target_wstrb;
    assign target_rsp_ready = target_reset[1] && target_state == 2;

    always @(posedge source_clk or negedge reset_n) begin
        if (!reset_n) begin
            source_state <= 0; request_toggle <= 0; response_seen <= 0;
        end else if (source_reset[1]) case (source_state)
            0: if (source_req_valid) begin
                request_toggle <= !request_toggle; source_state <= 1;
            end
            1: if (response_sync[1] != response_seen) begin
                response_seen <= response_sync[1];
                if (source_req_valid && source_req_ready) begin
                    request_toggle <= !request_toggle; source_state <= 1;
                end else
                    source_state <= RESPONSE_CAPTURE_BYPASS != 0 && source_rsp_ready ? 0 : 2;
            end
            2: if (source_rsp_ready) begin
                if (source_req_valid && source_req_ready) begin
                    request_toggle <= !request_toggle; source_state <= 1;
                end else
                    source_state <= 0;
            end
            default: source_state <= 0;
        endcase
    end
    always @(posedge source_clk) begin
        if (source_req_valid && source_req_ready) begin
            held_addr <= source_req_addr; held_write <= source_req_write;
            held_wdata <= source_req_wdata; held_wstrb <= source_req_wstrb;
        end
        if (source_reset[1] && response_pending) begin
            source_response <= held_response; source_error <= held_error;
        end
    end
    always @(posedge target_clk or negedge reset_n) begin
        if (!reset_n) begin
            target_state <= 0; response_toggle <= 0; request_seen <= 0;
        end else if (target_reset[1]) case (target_state)
            0: if (request_sync[1] != request_seen) begin
                request_seen <= request_sync[1];
                target_state <= REQUEST_CAPTURE_BYPASS != 0 && target_req_ready ? 2 : 1;
            end
            1: if (target_req_ready) target_state <= 2;
            2: if (target_rsp_valid) begin
                response_toggle <= !response_toggle; target_state <= 0;
            end
            default: target_state <= 0;
        endcase
    end
    always @(posedge target_clk) begin
        if (target_reset[1] && request_pending) begin
            target_addr <= held_addr; target_write <= held_write;
            target_wdata <= held_wdata; target_wstrb <= held_wstrb;
        end
        if (target_rsp_valid && target_rsp_ready) begin
            held_response <= target_rsp_rdata; held_error <= target_rsp_error;
        end
    end
endmodule
