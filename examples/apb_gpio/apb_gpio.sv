module apb_gpio #(
    parameter int DATA_W = 8
) (
    input  logic              pclk,
    input  logic              presetn,

    input  logic               psel,
    input  logic               penable,
    input  logic               pwrite,
    input  logic [3:0]         paddr,
    input  logic [DATA_W-1:0]  pwdata,
    output logic [DATA_W-1:0]  prdata,
    output logic               pready,
    output logic               pslverr,

    output logic [DATA_W-1:0]  gpio_out
);

    logic [DATA_W-1:0] gpio_reg;

    always_ff @(posedge pclk or negedge presetn) begin
        if (!presetn)
            gpio_reg <= '0;
        else if (psel && penable && pwrite)
            gpio_reg <= pwdata;
    end

    assign prdata   = gpio_reg;
    assign pready   = 1'b1;
    assign pslverr  = 1'b0;
    assign gpio_out = gpio_reg;

endmodule
