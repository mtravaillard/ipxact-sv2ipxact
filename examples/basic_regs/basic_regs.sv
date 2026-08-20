module basic_regs (
    input  logic       clk,
    input  logic       rst_n,

    input  logic       enable,
    output logic [7:0] count
);

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            count <= '0;
        else if (enable)
            count <= count + 1'b1;
    end

endmodule
