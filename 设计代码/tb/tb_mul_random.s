`timescale 1ns/1ps

module tb_mul_random;
    reg clk = 0, rst_n = 0;
    always #5 clk = !clk;
    reg [31:0] instructions [0:1023];
    reg [31:0] operands_a [0:4095], operands_b [0:4095];
    reg [31:0] edges [0:15];
    wire [31:0] instruction_address, memory_address, memory_data;
    wire memory_valid, memory_write, trap;
    wire [3:0] memory_strobe;
    integer i, pair = 0, result_kind = 0, results = 0;
    reg [31:0] expected;
    reg [63:0] unsigned_product;
    reg signed [63:0] signed_a, signed_b, signed_product, mixed_product;

    function [31:0] swap_bytes;
        input [31:0] value;
        begin swap_bytes = {value[7:0], value[15:8], value[23:16], value[31:24]}; end
    endfunction

    wire [31:0] read_data = memory_address[2] ? operands_b[memory_address[14:3]] :
                                               operands_a[memory_address[14:3]];
    rv32i_pipeline_core dut (
        .clk(clk), .rst_n(rst_n), .imem_addr(instruction_address),
        .imem_rdata(swap_bytes(instructions[instruction_address[11:2]])),
        .imem_req_ready(1'b1), .imem_ready(1'b1), .imem_error(1'b0),
        .dmem_valid(memory_valid), .dmem_we(memory_write), .dmem_addr(memory_address),
        .dmem_wdata(memory_data), .dmem_wstrb(memory_strobe), .dmem_rdata(read_data),
        .dmem_ready(1'b1), .dmem_error(1'b0), .irq_timer(1'b0), .irq_external(1'b0),
        .trap_taken(trap)
    );

    always @(posedge clk) begin
        if (rst_n && trap) $fatal(1, "Random multiply program trapped at PC %h", instruction_address);
        if (rst_n && memory_valid && memory_write) begin
            if (memory_strobe != 4'hf) $fatal(1, "Unexpected masked multiply result");
            if (memory_address == 32'h100) begin
                if (memory_data != 32'h600d || results != 16384 || pair != 4096 || result_kind != 0)
                    $fatal(1, "Incomplete random multiplication coverage result=%h pairs=%0d results=%0d",
                           memory_data, pair, results);
                $display("PASS: 16384 actual MUL/MULH/MULHSU/MULHU results, 256 boundary operand pairs and 3840 deterministic random pairs");
                $finish;
            end
            if (memory_address != 32'h200 + result_kind*4 || pair >= 4096)
                $fatal(1, "Unexpected multiplication result order/address %h pair=%0d kind=%0d",
                       memory_address, pair, result_kind);
            unsigned_product = {32'd0, operands_a[pair]} * {32'd0, operands_b[pair]};
            signed_a = {{32{operands_a[pair][31]}}, operands_a[pair]};
            signed_b = {{32{operands_b[pair][31]}}, operands_b[pair]};
            signed_product = signed_a * signed_b;
            mixed_product = signed_a * $signed({32'd0, operands_b[pair]});
            case (result_kind)
                0: expected = unsigned_product[31:0];
                1: expected = signed_product[63:32];
                2: expected = mixed_product[63:32];
                3: expected = unsigned_product[63:32];
            endcase
            if (memory_data !== expected)
                $fatal(1, "Multiply mismatch pair=%0d kind=%0d A=%h B=%h actual=%h expected=%h",
                       pair, result_kind, operands_a[pair], operands_b[pair], memory_data, expected);
            results = results + 1;
            if (result_kind == 3) begin pair = pair + 1; result_kind = 0; end
            else result_kind = result_kind + 1;
        end
    end

    reg [31:0] rng = 32'h5a17c9e3;
    initial begin
        edges[0]=0; edges[1]=1; edges[2]=32'hffffffff; edges[3]=32'h80000000;
        edges[4]=32'h7fffffff; edges[5]=32'h0000ffff; edges[6]=32'h00010000;
        edges[7]=32'hffff0000; edges[8]=32'h00007fff; edges[9]=32'h00008000;
        edges[10]=32'hffff7fff; edges[11]=32'hffff8000; edges[12]=32'h55555555;
        edges[13]=32'haaaaaaaa; edges[14]=32'h00010001; edges[15]=32'hfffeffff;
        for (i = 0; i < 4096; i = i + 1) begin
            if (i < 256) begin operands_a[i]=edges[i/16]; operands_b[i]=edges[i%16]; end
            else begin
                rng=rng^(rng<<13); rng=rng^(rng>>17); rng=rng^(rng<<5); operands_a[i]=rng;
                rng=rng^(rng<<13); rng=rng^(rng>>17); rng=rng^(rng<<5); operands_b[i]=rng;
            end
        end
        for (i = 0; i < 1024; i = i + 1) instructions[i] = 32'h13000000;
        $readmemh("build/mul_random_test.hex", instructions);
        #12 rst_n = 1;
        #2000000;
        $fatal(1, "Random multiplication program timed out at pair=%0d PC=%h", pair, instruction_address);
    end
endmodule
