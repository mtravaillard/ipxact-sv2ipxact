cmake_minimum_required(VERSION 3.27)

# include(/home/maxime/Work/SoCMake/SoCMakeConfig.cmake)
include(./sv2ipxact.cmake)

find_package (Python3 COMPONENTS Interpreter Development)

project(ipxact_test)

enable_testing()

sv2ipxact(
    SV_FILE   "./adder/adder.v"
    META_FILE "./adder/adder.ipxact.json"
)

sv2ipxact(
    SV_FILE   "./openhwgroup/cva6.sv"
    META_FILE "./openhwgroup/cva6.ipxact.json"
)

sv2ipxact(
    SV_FILE   "./openhwgroup/ibex.sv"
    META_FILE "./openhwgroup/ibex.ipxact.json"
)

sv2ipxact(
    SV_FILE   "./apb_gpio/apb_gpio.sv"
    META_FILE "./apb_gpio/apb_gpio.ipxact.json"
)

sv2ipxact(
    SV_FILE   "./apb_gpio/apb_gpio_packed.sv"
    META_FILE "./apb_gpio/apb_gpio_packed.ipxact.json"
)

sv2ipxact_add_check_target()
