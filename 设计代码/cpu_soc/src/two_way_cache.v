`include "src/cpu_config.vh"
`timescale 1ns/1ps

// Blocking, write-through/no-write-allocate cache with synchronous word RAM.
// The backing port transfers complete 32-byte lines and reports write errors.
module two_way_cache #(
    parameter integer SET_BITS = 6,
    parameter integer READ_ONLY = 0,
    parameter integer FAST_RESPONSE = 0,
    parameter integer EARLY_RESTART = 0,
    parameter integer EARLY_WRITE_REQUEST = FAST_RESPONSE,
    parameter integer INGRESS_WRITE_REQUEST = 0,
    parameter integer FAST_WRITE_RESPONSE = 0,
    parameter integer REGISTER_TAG_LOOKUP = 0,
    parameter integer STATISTICS_ENABLE = 1
) (
    input wire clk,
    input wire rst_n,
    input wire invalidate,
    input wire req_valid,
    output wire req_ready,
    input wire [31:0] req_addr,
    input wire req_write,
    input wire [31:0] req_wdata,
    input wire [3:0] req_wstrb,
    input wire req_cacheable,
    output wire rsp_valid,
    input wire rsp_ready,
    output wire [31:0] rsp_rdata,
    output wire rsp_error,
    output wire mem_req_valid,
    input wire mem_req_ready,
    output wire [31:0] mem_req_addr,
    output wire mem_req_write,
    output wire [255:0] mem_req_wdata,
    output wire [31:0] mem_req_wstrb,
    input wire mem_rsp_valid,
    output wire mem_rsp_ready,
    input wire [255:0] mem_rsp_rdata,
    input wire mem_rsp_error,
    output reg [31:0] hits,
    output reg [31:0] misses
);
    localparam integer SETS = 1 << SET_BITS;
    localparam integer TAG_BITS = 27 - SET_BITS;
    localparam IDLE = 0, LOOKUP = 1, MEMORY_REQUEST = 2, MEMORY_WAIT = 3,
               FILL = 4, RESPONSE = 5;
    reg [2:0] state;
    reg response_valid, response_error;
    reg [31:0] response_data;
    reg [SETS-1:0] valid0, valid1, lru;
`ifdef CPU_CACHE_TAGS_LUTRAM
    (* syn_ramstyle = "lut_ram" *) reg [TAG_BITS-1:0] tag0 [0:SETS-1];
    (* syn_ramstyle = "lut_ram" *) reg [TAG_BITS-1:0] tag1 [0:SETS-1];
`else
    reg [TAG_BITS-1:0] tag0 [0:SETS-1];
    reg [TAG_BITS-1:0] tag1 [0:SETS-1];
`endif
    wire [31:0] read0, read1;
    reg [31:0] address, write_data;
    reg [3:0] write_strobe;
    reg write_request, cacheable, killed;
    reg fill_way, store_hit, store_way, ingress_issued;
    reg [255:0] fill_data;
    reg [2:0] fill_word;

    wire [SET_BITS-1:0] request_set = address[SET_BITS+4:5];
    wire [TAG_BITS-1:0] request_tag = address[31:SET_BITS+5];
    wire [TAG_BITS-1:0] lookup_tag0, lookup_tag1;
    wire lookup_valid0, lookup_valid1;
    // Read metadata with the accepted request, alongside the synchronous data RAM.
    generate if (REGISTER_TAG_LOOKUP != 0) begin : registered_tags
        reg [TAG_BITS-1:0] value0, value1;
        reg valid_value0, valid_value1;
        always @(posedge clk) begin
            if (req_valid && req_ready) begin
                value0 <= tag0[req_addr[SET_BITS+4:5]];
                value1 <= tag1[req_addr[SET_BITS+4:5]];
                valid_value0 <= valid0[req_addr[SET_BITS+4:5]];
                valid_value1 <= valid1[req_addr[SET_BITS+4:5]];
            end
        end
        assign lookup_tag0 = value0;
        assign lookup_tag1 = value1;
        assign lookup_valid0 = valid_value0;
        assign lookup_valid1 = valid_value1;
    end else begin : asynchronous_tags
        assign lookup_tag0 = tag0[request_set];
        assign lookup_tag1 = tag1[request_set];
        assign lookup_valid0 = valid0[request_set];
        assign lookup_valid1 = valid1[request_set];
    end endgenerate
    wire tag_hit0 = lookup_tag0 == request_tag;
    wire tag_hit1 = lookup_tag1 == request_tag;
    wire hit0 = cacheable && lookup_valid0 && tag_hit0;
    wire hit1 = cacheable && lookup_valid1 && tag_hit1;
    wire hit = hit0 || hit1;
    wire victim = !valid0[request_set] ? 1'b0 : !valid1[request_set] ? 1'b1 : lru[request_set];
    wire [SET_BITS+2:0] read_index = req_valid && req_ready ? req_addr[SET_BITS+4:2] : address[SET_BITS+4:2];
    wire [SET_BITS+2:0] fill_index = {request_set, fill_word};

    // The load-data mux must not sit behind the shared `lookup_hit` net.  Every
    // term here except the tag compare is already registered, so the tool can
    // fold them into the comparator's own LUT and drive the mux directly.
    wire lookup_enable = state == LOOKUP && !write_request && !killed && !invalidate;
    wire lookup_hit0 = lookup_enable && cacheable && lookup_valid0 && tag_hit0;
    wire lookup_hit1 = lookup_enable && cacheable && lookup_valid1 && tag_hit1;
    wire lookup_hit = lookup_hit0 || lookup_hit1;
    wire lookup_store_pending = FAST_WRITE_RESPONSE != 0 && state == LOOKUP &&
                                write_request && READ_ONLY == 0 && ingress_issued;
    wire lookup_store_response = lookup_store_pending && mem_rsp_valid;
    wire memory_response = state == MEMORY_WAIT && mem_rsp_valid;
    wire refill_response = !write_request && cacheable && !mem_rsp_error && !killed && !invalidate;
    wire direct_response = lookup_store_response || (memory_response &&
        (EARLY_RESTART != 0 || (FAST_RESPONSE != 0 && !refill_response)));
    assign req_ready = (state == IDLE || (lookup_hit && rsp_ready)) && !invalidate;
    assign rsp_valid = lookup_hit || response_valid || direct_response;
    assign rsp_rdata = lookup_hit0 ? read0 : lookup_hit1 ? read1 : direct_response ?
        (write_request ? 32'd0 : mem_rsp_rdata[address[4:2]*32 +: 32]) : response_data;
    // Accepted requests clear response_error, so a lookup hit cannot inherit a fault.
    assign rsp_error = direct_response ? mem_rsp_error : response_error;
    wire ingress_store_request = INGRESS_WRITE_REQUEST != 0 && req_valid && req_ready &&
                                 req_write && READ_ONLY == 0;
    wire early_store_request = (EARLY_WRITE_REQUEST != 0 || INGRESS_WRITE_REQUEST != 0) &&
                               state == LOOKUP && write_request && READ_ONLY == 0 && !ingress_issued;
    assign mem_req_valid = state == MEMORY_REQUEST || early_store_request || ingress_store_request;
    assign mem_req_addr = ingress_store_request ? {req_addr[31:5], 5'd0} : {address[31:5], 5'd0};
    assign mem_req_write = ingress_store_request || write_request;
    // Byte strobes select the addressed word; masked words may share its data.
    assign mem_req_wdata = {8{ingress_store_request ? req_wdata : write_data}};
    assign mem_req_wstrb = ingress_store_request ?
        {28'd0, req_wstrb} << (req_addr[4:2] * 4) : {28'd0, write_strobe} << (address[4:2] * 4);
    assign mem_rsp_ready = state == MEMORY_WAIT || lookup_store_pending;

    wire data_write_valid = rst_n && !killed && !invalidate &&
        (state == FILL || (mem_rsp_valid && write_request && !mem_rsp_error &&
                          ((state == MEMORY_WAIT && store_hit) || (lookup_store_response && hit))));
    wire data_write_way = state == FILL ? fill_way : lookup_store_response ? hit1 : store_way;
    wire [SET_BITS+2:0] data_write_index = state == FILL ? fill_index : address[SET_BITS+4:2];
    wire [31:0] data_write_value = state == FILL ? fill_data[fill_word*32 +: 32] : write_data;
    wire [3:0] data_write_mask = state == FILL ? 4'hf : write_strobe;
    genvar byte_number;
    // One write port per byte bank gives PDS an unambiguous block-RAM template.
    generate for (byte_number = 0; byte_number < 4; byte_number = byte_number + 1) begin : byte_bank
        reg [7:0] data0 [0:SETS*8-1];
        reg [7:0] data1 [0:SETS*8-1];
        reg [7:0] value0, value1;
        assign read0[byte_number*8 +: 8] = value0;
        assign read1[byte_number*8 +: 8] = value1;
        always @(posedge clk) begin
            value0 <= data0[read_index];
            value1 <= data1[read_index];
            if (data_write_valid && data_write_mask[byte_number]) begin
                if (data_write_way) data1[data_write_index] <= data_write_value[byte_number*8 +: 8];
                else data0[data_write_index] <= data_write_value[byte_number*8 +: 8];
            end
        end
    end endgenerate
    always @(posedge clk) begin
        if (rst_n && state == FILL && fill_word == 7 && !killed && !invalidate) begin
            if (fill_way) tag1[request_set] <= request_tag;
            else tag0[request_set] <= request_tag;
        end
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= IDLE;
            valid0 <= 0;
            valid1 <= 0;
            lru <= 0;
            address <= 0;
            write_data <= 0;
            write_strobe <= 0;
            write_request <= 0;
            cacheable <= 0;
            killed <= 0;
            store_hit <= 0;
            store_way <= 0;
            ingress_issued <= 0;
            fill_way <= 0;
            fill_word <= 0;
            fill_data <= 0;
            response_valid <= 0;
            response_error <= 0;
            response_data <= 0;
            hits <= 0;
            misses <= 0;
        end else begin
            case (state)
                IDLE: state <= IDLE;
                LOOKUP: begin
                    if (cacheable && !killed) begin
                        if (hit) begin
                            if (STATISTICS_ENABLE != 0) hits <= hits + 1'b1;
                            lru[request_set] <= hit0;
                        end else if (STATISTICS_ENABLE != 0) misses <= misses + 1'b1;
                    end
                    if (write_request && READ_ONLY != 0) begin
                        response_data <= 0;
                        response_error <= 1;
                        response_valid <= 1;
                        state <= RESPONSE;
                    end else if (lookup_store_response) begin
                        response_data <= 0;
                        response_error <= mem_rsp_error;
                        response_valid <= !rsp_ready;
                        state <= rsp_ready ? IDLE : RESPONSE;
                        if (mem_rsp_error && hit) begin
                            if (hit1) valid1[request_set] <= 0;
                            else valid0[request_set] <= 0;
                        end
                    end else if (!write_request && hit && !killed && !invalidate) begin
                        if (rsp_ready) state <= IDLE;
                        else begin
                            response_data <= hit0 ? read0 : read1;
                            response_valid <= 1;
                            state <= RESPONSE;
                        end
                    end else begin
                        store_hit <= hit;
                        store_way <= hit1;
                        fill_way <= victim;
                        if (!write_request && cacheable) begin
                            if (victim) valid1[request_set] <= 0;
                            else valid0[request_set] <= 0;
                        end
                        state <= ingress_issued || (early_store_request && mem_req_ready) ?
                                 MEMORY_WAIT : MEMORY_REQUEST;
                    end
                end
                MEMORY_REQUEST: if (mem_req_ready) state <= MEMORY_WAIT;
                MEMORY_WAIT: if (mem_rsp_valid) begin
                    response_error <= mem_rsp_error;
                    response_data <= write_request ? 32'd0 : mem_rsp_rdata[address[4:2]*32 +: 32];
                    if (mem_rsp_error && write_request && store_hit) begin
                        if (store_way) valid1[request_set] <= 0;
                        else valid0[request_set] <= 0;
                    end
                    if (refill_response) begin
                        fill_data <= mem_rsp_rdata;
                        fill_word <= 0;
                        state <= FILL;
                        // Return the demanded word while the line RAM fills in the background.
                        if (EARLY_RESTART != 0) response_valid <= !rsp_ready;
                    end else begin
                        response_valid <= !(direct_response && rsp_ready);
                        state <= direct_response && rsp_ready ? IDLE : RESPONSE;
                    end
                end
                FILL: begin
                    if (response_valid && rsp_ready) response_valid <= 0;
                    if (fill_word == 7) begin
                        if (!killed && !invalidate) begin
                            if (fill_way) valid1[request_set] <= 1;
                            else valid0[request_set] <= 1;
                            lru[request_set] <= !fill_way;
                        end
                        if (EARLY_RESTART != 0)
                            state <= !response_valid || rsp_ready ? IDLE : RESPONSE;
                        else begin
                            response_valid <= 1;
                            state <= RESPONSE;
                        end
                    end else fill_word <= fill_word + 1'b1;
                end
                RESPONSE: if (rsp_ready) begin
                    response_valid <= 0;
                    state <= IDLE;
                end
                default: state <= IDLE;
            endcase
            if (req_valid && req_ready) begin
                address <= req_addr;
                write_data <= req_wdata;
                write_strobe <= req_wstrb;
                write_request <= req_write;
                cacheable <= req_cacheable;
                // Write-through does not need the hit result before starting the backing write.
                ingress_issued <= ingress_store_request && mem_req_ready;
                killed <= 0;
                response_error <= 0;
                state <= LOOKUP;
            end
            // A fill started before invalidation must not revalidate old data.
            if (invalidate) begin
                valid0 <= 0;
                valid1 <= 0;
                if (state != IDLE) killed <= 1;
            end
        end
    end
endmodule

