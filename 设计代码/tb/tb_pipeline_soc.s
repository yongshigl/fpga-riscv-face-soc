`timescale 1ns/1ps

module tb_pipeline_soc;
    reg clk = 1'b0;
    reg rst_n = 1'b0;
    wire [31:0] gpio_out;
    wire uart_tx_pin;
    wire [7:0] uart_debug_data;
    wire uart_debug_strobe;
    wire [31:0] current_pc;
    wire trap_taken;
    wire [63:0] cycle_count;
    wire [63:0] instret_count;
    integer trap_count = 0;
    reg saw_uart = 1'b0;

    rv32i_pipeline_soc #(
        .IMEM_WORDS(1024), .DMEM_WORDS(256),
        .IMEM_INIT_FILE("build/pipeline_soc_test.hex"),
        .CLOCK_HZ(400), .UART_BAUD(100)
    ) dut (
        .clk(clk), .rst_n(rst_n), .external_irq(1'b0), .gpio_out(gpio_out),
        .uart_tx_pin(uart_tx_pin), .uart_debug_data(uart_debug_data),
        .uart_debug_strobe(uart_debug_strobe), .current_pc(current_pc),
        .trap_taken(trap_taken), .cycle_count(cycle_count), .instret_count(instret_count)
    );

    always #5 clk = ~clk;

    always @(posedge clk) begin
        if (trap_taken)
            trap_count <= trap_count + 1;
        if (uart_debug_strobe && uart_debug_data == 8'h41)
            saw_uart <= 1'b1;
        if (gpio_out == 32'h000051c0) begin
            if (trap_count != 1 || !saw_uart) begin
                $display("FAIL: pipeline SoC traps=%0d uart=%b pc=%h", trap_count, saw_uart, current_pc);
                $fatal(1);
            end
            $display("PASS: pipeline SoC gpio=%h traps=%0d cycles=%0d instret=%0d", gpio_out, trap_count, cycle_count, instret_count);
            $finish;
        end
        if (gpio_out == 32'h0000dead) begin
            $display("FAIL: pipeline SoC firmware reported failure pc=%h", current_pc);
            $fatal(1);
        end
    end

    initial begin
        $dumpfile("build/pipeline_soc.vcd");
        $dumpvars(0, tb_pipeline_soc);
        #12 rst_n = 1'b1;
        #30000;
        $display("FAIL: pipeline SoC timeout pc=%h gpio=%h", current_pc, gpio_out);
        $fatal(1);
    end
endmodule
