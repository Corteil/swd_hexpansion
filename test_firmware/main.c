// Test image for the SWD programmer: blinks the on-board LED and talks on UART0.
//
//   LED   GPIO 25 (Pico 2; the Pico 2 W's LED is on the radio chip and will not blink)
//   UART0 GPIO 0 = TX, GPIO 1 = RX, 115200 8N1
//
// Every half second the LED toggles and "tick N" is sent. Anything received is echoed
// back with a "> " prefix, so a loopback or a terminal on the other end can be checked.
#include <stdio.h>
#include "pico/stdlib.h"
#include "hardware/uart.h"
#include "hardware/gpio.h"

#define LED_PIN 25
#define UART_ID uart0
#define UART_TX_PIN 0
#define UART_RX_PIN 1
#define BAUD 115200

int main(void) {
    gpio_init(LED_PIN);
    gpio_set_dir(LED_PIN, GPIO_OUT);

    uart_init(UART_ID, BAUD);
    gpio_set_function(UART_TX_PIN, UART_FUNCSEL_NUM(UART_ID, UART_TX_PIN));
    gpio_set_function(UART_RX_PIN, UART_FUNCSEL_NUM(UART_ID, UART_RX_PIN));

    uart_puts(UART_ID, "\r\nswd_test started\r\n");

    uint32_t n = 0;
    bool led = false;
    absolute_time_t next = make_timeout_time_ms(500);
    while (true) {
        while (uart_is_readable(UART_ID)) {
            char c = uart_getc(UART_ID);
            uart_puts(UART_ID, "> ");
            uart_putc_raw(UART_ID, c);
            uart_puts(UART_ID, "\r\n");
        }
        if (absolute_time_diff_us(get_absolute_time(), next) <= 0) {
            next = delayed_by_ms(next, 500);
            led = !led;
            gpio_put(LED_PIN, led);
            char line[32];
            snprintf(line, sizeof line, "tick %lu\r\n", (unsigned long)n++);
            uart_puts(UART_ID, line);
        }
    }
}
