`timescale 1ns/1ps

// Synchronous ROM/RAM line refill, plus an optional external DDR address range.
module memory_line_backend #(
    parameter integer ROM_WORDS = 4096,
    parameter integer RAM_WORDS = 2048,
    parameter integer FAST_WRITE_RESPONSE = 0,
    parameter ROM_INIT_FILE = "",
    parameter RAM_INIT_FILE = ""
) (
    input wire clk, rst_n,
    input wire req_valid,
    output wire req_ready,
    input wire req_write,
    input wire [31:0] req_addr,
    input wire [255:0] req_wdata,
    input wire [31:0] req_wstrb,
    output wire rsp_valid,
    input wire rsp_ready,
    output wire [255:0] rsp_rdata,
    output wire rsp_error,
    output wire external_req_valid,
    input wire external_req_ready,
    output wire external_req_write,
    output wire [31:0] external_req_addr,
    output wire [255:0] external_req_wdata,
    output wire [31:0] external_req_wstrb,
    input wire external_rsp_valid,
    output wire external_rsp_ready,
    input wire [255:0] external_rsp_rdata,
    input wire external_rsp_error
);
    localparam IDLE = 0, READ_WORDS = 1, WRITE_WORDS = 2,
               RESPONSE = 3, EXTERNAL_REQUEST = 4, EXTERNAL_WAIT = 5;
    localparam ROM_BITS = $clog2(ROM_WORDS), RAM_BITS = $clog2(RAM_WORDS);
    reg [2:0] state;
    reg [31:0] address;
    reg [255:0] write_data, response_data;
    reg [31:0] write_mask;
    reg write_request, ram_selected, response_error;
    reg [3:0] issue_word;
    reg read_pending;
    reg [31:0] instruction_mem [0:ROM_WORDS-1];
    reg [31:0] data_mem [0:RAM_WORDS-1];
    reg [31:0] rom_read, ram_read;
    wire [ROM_BITS-1:0] rom_index = {address[ROM_BITS+1:5], issue_word[2:0]};
    wire [RAM_BITS-1:0] ram_index = {address[RAM_BITS+1:5], issue_word[2:0]};
    wire [31:0] rom_word = {rom_read[7:0], rom_read[15:8], rom_read[23:16], rom_read[31:24]};
    wire [2:0] capture_word = issue_word[2:0] - 1'b1;
    function [3:0] next_masked_word;
        input [31:0] mask;
        input [3:0] first;
        integer word_number;
        begin
            next_masked_word = 8;
            for (word_number = 7; word_number >= 0; word_number = word_number - 1)
                if (word_number >= first && |mask[word_number*4 +: 4])
                    next_masked_word = word_number;
        end
    endfunction
    wire [3:0] following_write_word = next_masked_word(write_mask, issue_word + 1'b1);
    integer index, byte_number;
    initial begin
        if (ROM_INIT_FILE != "") $readmemh(ROM_INIT_FILE, instruction_mem);
        else for (index = 0; index < ROM_WORDS; index = index + 1) instruction_mem[index] = 32'h13000000;
        if (RAM_INIT_FILE != "") $readmemh(RAM_INIT_FILE, data_mem);
        else for (index = 0; index < RAM_WORDS; index = index + 1) data_mem[index] = 0;
    end
    always @(posedge clk) begin
        rom_read <= instruction_mem[rom_index];
        ram_read <= data_mem[ram_index];
        if (rst_n && state == WRITE_WORDS)
            for (byte_number = 0; byte_number < 4; byte_number = byte_number + 1)
                if (write_mask[issue_word*4 + byte_number])
                    data_mem[ram_index][byte_number*8 +: 8] <=
                        write_data[issue_word*32 + byte_number*8 +: 8];
    end
    assign req_ready = state == IDLE;
    // The final masked RAM word and its response commit on the same clock edge.
    wire write_complete = state == WRITE_WORDS && following_write_word == 8;
    assign rsp_valid = state == RESPONSE || (FAST_WRITE_RESPONSE != 0 && write_complete) ||
                       (state == EXTERNAL_WAIT && external_rsp_valid);
    assign rsp_rdata = state == EXTERNAL_WAIT ? external_rsp_rdata : response_data;
    assign rsp_error = state == EXTERNAL_WAIT ? external_rsp_error : response_error;
    assign external_req_valid = state == EXTERNAL_REQUEST;
    assign external_req_write = write_request;
    assign external_req_addr = address;
    assign external_req_wdata = write_data;
    assign external_req_wstrb = write_mask;
    assign external_rsp_ready = state == EXTERNAL_WAIT && rsp_ready;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= IDLE;
            address <= 0;
            write_data <= 0;
            write_mask <= 0;
            write_request <= 0;
            ram_selected <= 0;
            response_data <= 0;
            response_error <= 0;
            issue_word <= 0;
            read_pending <= 0;
        end else case (state)
            IDLE: if (req_valid) begin
                address <= req_addr;
                write_data <= req_wdata;
                write_mask <= req_wstrb;
                write_request <= req_write;
                ram_selected <= req_addr >= 32'h10000000 && req_addr < 32'h10000000 + RAM_WORDS*4;
                response_error <= 0;
                response_data <= 0;
                issue_word <= 0;
                read_pending <= 0;
                if (req_addr[4:0] != 0) begin
                    response_error <= 1;
                    state <= RESPONSE;
                end else if (req_addr[31:30] == 2'b10) state <= EXTERNAL_REQUEST;
                else if (req_addr < ROM_WORDS*4) begin
                    if (req_write) begin
                        response_error <= 1;
                        state <= RESPONSE;
                    end else state <= READ_WORDS;
                end else if (req_addr >= 32'h10000000 && req_addr < 32'h10000000 + RAM_WORDS*4) begin
                    state <= req_write ? (req_wstrb == 0 ? RESPONSE : WRITE_WORDS) : READ_WORDS;
                    if (req_write) issue_word <= next_masked_word(req_wstrb, 0);
                end
                else begin
                    response_error <= 1;
                    state <= RESPONSE;
                end
            end
            READ_WORDS: begin
                if (read_pending)
                    response_data[capture_word*32 +: 32] <= ram_selected ? ram_read : rom_word;
                if (issue_word == 8) begin
                    state <= RESPONSE;
                    read_pending <= 0;
                end else begin
                    issue_word <= issue_word + 1'b1;
                    read_pending <= 1;
                end
            end
            WRITE_WORDS: if (following_write_word == 8)
                             state <= FAST_WRITE_RESPONSE != 0 && rsp_ready ? IDLE : RESPONSE;
                         else issue_word <= following_write_word;
            RESPONSE: if (rsp_ready) state <= IDLE;
            EXTERNAL_REQUEST: if (external_req_ready) state <= EXTERNAL_WAIT;
            EXTERNAL_WAIT: if (external_rsp_valid && rsp_ready) state <= IDLE;
            default: state <= IDLE;
        endcase
    end
endmodule
