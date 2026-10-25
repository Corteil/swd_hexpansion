cmake_minimum_required(VERSION 3.13)

set(PICO_BOARD pico2)
include(pico_sdk_import.cmake)

project(swd_test C CXX ASM)
set(CMAKE_C_STANDARD 11)
pico_sdk_init()

add_executable(swd_test main.c)
target_link_libraries(swd_test pico_stdlib)

# stdio is not used: the serial test drives UART0 directly
pico_enable_stdio_usb(swd_test 0)
pico_enable_stdio_uart(swd_test 0)

pico_add_extra_outputs(swd_test)
