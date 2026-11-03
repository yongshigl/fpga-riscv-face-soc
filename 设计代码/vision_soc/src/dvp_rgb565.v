`timescale 1ns/1ps

module dvp_rgb565 (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        configured,
    input  wire        vsync,
    input  wire        href,
    input  wire [7:0]  data,
    output reg         pixel_vsync,
    output reg         pixel_href,
    output reg         pixel_valid,
    output reg  [15:0] pixel
);
    reg [1:0] configured_sync;
    reg second_byte;
    reg [7:0] first_byte;

    // Synchronization signals have the same latency as the assembled pixel.
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            configured_sync <= 0;
            second_byte <= 0;
            first_byte <= 0;
            pixel_vsync <= 1;
            pixel_href <= 0;
            pixel_valid <= 0;
            pixel <= 0;
        end else begin
            configured_sync <= {configured_sync[0], configured};
            pixel_vsync <= vsync;
            pixel_href <= href;
            pixel_valid <= 0;
            if (vsync || !href || !configured_sync[1]) begin
                second_byte <= 0;
            end else if (!second_byte) begin
                first_byte <= data;
                second_byte <= 1;
            end else begin
                pixel <= {first_byte, data};
                pixel_valid <= 1;
                second_byte <= 0;
            end
        end
    end
endmodule
