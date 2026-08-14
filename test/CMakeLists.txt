cmake_minimum_required(VERSION 3.27)

# include(/home/maxime/Work/SoCMake/SoCMakeConfig.cmake)
include(./sv2ipxact.cmake)

find_package (Python3 COMPONENTS Interpreter Development)

project(ipxact_test)

sv2ipxact(
    SV_FILE "./adder/adder.v"
    VENDOR "CERN"
    LIBRARY "IP_TEST"
)

sv2ipxact(
    SV_FILE "./openhwgroup/cva6.sv"
    VENDOR "openhwgroup"
    LIBRARY "core"
)

sv2ipxact(
    SV_FILE "./openhwgroup/ibex.sv"
    VENDOR "openhwgroup"
    LIBRARY "core"
)
