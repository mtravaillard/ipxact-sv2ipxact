package apb_gpio_packed_pkg;

    typedef struct packed {
        logic       psel;
        logic       penable;
        logic       pwrite;
        logic [3:0] paddr;
        logic [7:0] pwdata;
    } apb_req_t;

    typedef struct packed {
        logic [7:0] prdata;
        logic       pready;
        logic       pslverr;
    } apb_rsp_t;

endpackage

module apb_gpio_packed
    import apb_gpio_packed_pkg::*;
(
    input  logic     pclk,
    input  logic     presetn,

    input  apb_req_t apb_req_i,
    output apb_rsp_t apb_rsp_o,

    output logic [7:0] gpio_out
);

    logic [7:0] gpio_reg;

    always_ff @(posedge pclk or negedge presetn) begin
        if (!presetn)
            gpio_reg <= '0;
        else if (apb_req_i.psel && apb_req_i.penable && apb_req_i.pwrite)
            gpio_reg <= apb_req_i.pwdata;
    end

    assign apb_rsp_o.prdata  = gpio_reg;
    assign apb_rsp_o.pready  = 1'b1;
    assign apb_rsp_o.pslverr = 1'b0;
    assign gpio_out          = gpio_reg;

endmodule
