`timescale 1ns/1ps

module camera_capture_gate #(
    parameter integer WARMUP_FRAMES = 10
) (
    input wire pixel_clk, rst_n,
    input wire configured, vsync,
    input wire request_toggle,
    input wire tensor_done_toggle, frame_done_toggle,
    output wire capture_enable,
    output reg acknowledge_toggle
);
    localparam IDLE = 0, ARMED = 1, CAPTURING = 2, DRAIN = 3;
    reg [1:0] state;
    (* ASYNC_REG = "TRUE" *) reg [1:0] request_sync, tensor_sync, frame_sync, configured_sync;
    reg vsync_last;
    reg [4:0] warmup_count;
    reg tensor_before, frame_before, active_request;
    assign capture_enable = configured_sync[1] && warmup_count >= WARMUP_FRAMES &&
                            (state == ARMED || state == CAPTURING);

    always @(posedge pixel_clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= IDLE;
            request_sync <= 0; tensor_sync <= 0; frame_sync <= 0; configured_sync <= 0;
            vsync_last <= 1; warmup_count <= 0;
            tensor_before <= 0; frame_before <= 0; active_request <= 0;
            acknowledge_toggle <= 0;
        end else begin
            request_sync <= {request_sync[0], request_toggle};
            tensor_sync <= {tensor_sync[0], tensor_done_toggle};
            frame_sync <= {frame_sync[0], frame_done_toggle};
            configured_sync <= {configured_sync[0], configured};
            vsync_last <= vsync;
            if (configured_sync[1] && vsync_last && !vsync && warmup_count != 31)
                warmup_count <= warmup_count + 1'b1;
            case (state)
                IDLE: if (request_sync[1] != acknowledge_toggle) begin
                    active_request <= request_sync[1];
                    tensor_before <= tensor_sync[1];
                    frame_before <= frame_sync[1];
                    state <= ARMED;
                end
                ARMED: if (capture_enable && vsync_last && !vsync) state <= CAPTURING;
                CAPTURING: if (!vsync_last && vsync) state <= DRAIN;
                DRAIN: if (tensor_sync[1] != tensor_before && frame_sync[1] != frame_before) begin
                    acknowledge_toggle <= active_request;
                    state <= IDLE;
                end
                default: state <= IDLE;
            endcase
        end
    end
endmodule
