`timescale 1ns/1ps

module tb_coremark;
    reg clk = 1'b0;
    reg rst_n = 1'b0;
    wire [31:0] gpio_out;
    wire [7:0] uart_debug_data;
    wire uart_debug_strobe;
    wire [31:0] current_pc;
    wire trap_taken;
    wire [63:0] cycle_count;
    wire [63:0] instret_count;
    integer trap_count = 0;

    rv32i_pipeline_soc #(
        .IMEM_WORDS(16384), .DMEM_WORDS(8192),
        .IMEM_INIT_FILE("build/coremark_text.hex"),
        .DMEM_INIT_FILE("build/coremark_data.hex"),
        .CLOCK_HZ(400), .UART_BAUD(100)
    ) dut (
        .clk(clk), .rst_n(rst_n), .external_irq(1'b0), .gpio_out(gpio_out),
        .uart_tx_pin(), .uart_debug_data(uart_debug_data),
        .uart_debug_strobe(uart_debug_strobe), .current_pc(current_pc),
        .trap_taken(trap_taken), .cycle_count(cycle_count), .instret_count(instret_count)
    );

    always #5 clk = ~clk;

    always @(posedge clk) begin
        if (trap_taken)
            trap_count <= trap_count + 1;
        if (uart_debug_strobe)
            $write("%c", uart_debug_data);
        if (gpio_out == 32'h0000c0de) begin
            $display("\nPASS: CoreMark CRC validated cycles=%0d instret=%0d CPI=%0d.%03d",
                     cycle_count, instret_count, cycle_count / instret_count,
                     ((cycle_count % instret_count) * 1000) / instret_count);
            $finish;
        end
        if (gpio_out == 32'h0000dead || trap_count != 0) begin
            $display("\nFAIL: CoreMark gpio=%h traps=%0d pc=%h", gpio_out, trap_count, current_pc);
            $fatal(1);
        end
    end

    initial begin
        #12 rst_n = 1'b1;
        #500000000;
        $display("\nFAIL: CoreMark timeout pc=%h cycles=%0d instret=%0d", current_pc, cycle_count, instret_count);
        $fatal(1);
    end
endmodule
