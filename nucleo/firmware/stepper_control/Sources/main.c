#if !defined(STM32F401xE)
#define STM32F401xE
#endif

#include "stm32f4xx.h"
#include <string.h>
#include <stdlib.h>

/* === Pin Mapping ===
 * Motor 1 (PAN):  DIR = PA10 (D2), STEP = PB3 (D3)
 * Motor 2 (TILT): DIR = PB5  (D4), STEP = PB4 (D5)
 * Microsteps:     M2=PA9 (D8), M1=PC7 (D9), M0=PB6 (D10)
 * Limit Switches: PAN_NEG=PA7 (D11), TILT_NEG=PA8 (D7)
 *                 PAN_POS=PB10 (D6), TILT_POS=PA6 (D12)
 * USART2:         TX=PA2 (D1), RX=PA3 (D0)
 */

#define M0_PORT GPIOB
#define M0_PIN  6
#define M1_PORT GPIOC
#define M1_PIN  7
#define M2_PORT GPIOA
#define M2_PIN  9

/* Limit switches - negative direction (home) - D11/PA7 stops leftward motion */
#define PAN_NEG_PORT    GPIOA
#define PAN_NEG_PIN     7
#define TILT_NEG_PORT   GPIOA
#define TILT_NEG_PIN    8

/* Limit switches - positive direction - D6/PB10 stops rightward motion */
#define PAN_POS_PORT    GPIOB
#define PAN_POS_PIN     10
#define TILT_POS_PORT   GPIOA
#define TILT_POS_PIN    6

/* UART Buffer */
#define UART_BUF_SIZE 64
volatile char rx_buffer[UART_BUF_SIZE];
volatile uint8_t rx_index = 0;
volatile uint8_t cmd_ready = 0;

/* Position Tracking */
static int32_t pan_position = 0;
static int32_t tilt_position = 0;
static uint8_t pan_homed = 0;
static uint8_t tilt_homed = 0;

/* Software Limits (steps from home position)
 * After homing, position is 0 at the left limit switch.
 * Physical travel is ~4255 steps (measured 2025-12-08).
 * Set soft limits slightly inside physical limits for safety.
 */
#define PAN_LIMIT_MIN   0       // At home/left limit
#define PAN_LIMIT_MAX   4200    // Just before right limit
#define TILT_LIMIT_MIN  -2000
#define TILT_LIMIT_MAX   2000

/* === Delay === */
static void delay_cycles(volatile uint32_t cycles) {
    while (cycles--) { __NOP(); }
}

/* === GPIO Init === */
static void gpio_init(void) {
    RCC->AHB1ENR |= RCC_AHB1ENR_GPIOAEN | RCC_AHB1ENR_GPIOBEN | RCC_AHB1ENR_GPIOCEN;

    // Motor DIR/STEP outputs
    GPIOA->MODER &= ~(3U << (10 * 2));
    GPIOA->MODER |=  (1U << (10 * 2));  // PA10 output

    GPIOB->MODER &= ~((3U << (3*2)) | (3U << (4*2)) | (3U << (5*2)));
    GPIOB->MODER |=  ((1U << (3*2)) | (1U << (4*2)) | (1U << (5*2)));

    // Microstep outputs
    M0_PORT->MODER &= ~(3U << (M0_PIN * 2)); M0_PORT->MODER |= (1U << (M0_PIN * 2));
    M1_PORT->MODER &= ~(3U << (M1_PIN * 2)); M1_PORT->MODER |= (1U << (M1_PIN * 2));
    M2_PORT->MODER &= ~(3U << (M2_PIN * 2)); M2_PORT->MODER |= (1U << (M2_PIN * 2));

    // USART2 (PA2=TX, PA3=RX) -> AF7
    GPIOA->MODER &= ~((3U << (2*2)) | (3U << (3*2)));
    GPIOA->MODER |=  ((2U << (2*2)) | (2U << (3*2)));
    GPIOA->AFR[0] |= (7U << (2*4)) | (7U << (3*4));

    // Limit switch inputs with pull-ups (active-low)
    // PAN negative limit (D6 = PB10)
    PAN_NEG_PORT->MODER &= ~(3U << (PAN_NEG_PIN * 2));
    PAN_NEG_PORT->PUPDR &= ~(3U << (PAN_NEG_PIN * 2));
    PAN_NEG_PORT->PUPDR |=  (1U << (PAN_NEG_PIN * 2));

    // TILT negative limit (D7 = PA8)
    TILT_NEG_PORT->MODER &= ~(3U << (TILT_NEG_PIN * 2));
    TILT_NEG_PORT->PUPDR &= ~(3U << (TILT_NEG_PIN * 2));
    TILT_NEG_PORT->PUPDR |=  (1U << (TILT_NEG_PIN * 2));

    // PAN positive limit (D11 = PA7)
    PAN_POS_PORT->MODER &= ~(3U << (PAN_POS_PIN * 2));
    PAN_POS_PORT->PUPDR &= ~(3U << (PAN_POS_PIN * 2));
    PAN_POS_PORT->PUPDR |=  (1U << (PAN_POS_PIN * 2));

    // TILT positive limit (D12 = PA6)
    TILT_POS_PORT->MODER &= ~(3U << (TILT_POS_PIN * 2));
    TILT_POS_PORT->PUPDR &= ~(3U << (TILT_POS_PIN * 2));
    TILT_POS_PORT->PUPDR |=  (1U << (TILT_POS_PIN * 2));
}

/* === Limit Switch Reads (active-low: returns 1 when triggered) === */
static uint8_t read_pan_neg(void) {
    return ((PAN_NEG_PORT->IDR & (1U << PAN_NEG_PIN)) == 0) ? 1 : 0;
}
static uint8_t read_pan_pos(void) {
    return ((PAN_POS_PORT->IDR & (1U << PAN_POS_PIN)) == 0) ? 1 : 0;
}
static uint8_t read_tilt_neg(void) {
    return ((TILT_NEG_PORT->IDR & (1U << TILT_NEG_PIN)) == 0) ? 1 : 0;
}
static uint8_t read_tilt_pos(void) {
    return ((TILT_POS_PORT->IDR & (1U << TILT_POS_PIN)) == 0) ? 1 : 0;
}

/* === USART2 Init (115200 8N1 @ 16MHz HSI) === */
static void usart2_init(void) {
    RCC->APB1ENR |= RCC_APB1ENR_USART2EN;
    USART2->BRR = 0x8B;  // 16MHz / 115200
    USART2->CR1 |= USART_CR1_RE | USART_CR1_TE | USART_CR1_RXNEIE;
    USART2->CR1 |= USART_CR1_UE;
    NVIC_EnableIRQ(USART2_IRQn);
}

/* === USART2 IRQ Handler === */
void USART2_IRQHandler(void) {
    if (USART2->SR & USART_SR_RXNE) {
        char c = USART2->DR;
        if (cmd_ready) return;
        if (c == '\n' || c == '\r') {
            rx_buffer[rx_index] = '\0';
            if (rx_index > 0) cmd_ready = 1;
            rx_index = 0;
        } else if (rx_index < UART_BUF_SIZE - 1) {
            rx_buffer[rx_index++] = c;
        }
    }
}

/* === UART Output === */
static void uart_send_str(const char *str) {
    while (*str) {
        while (!(USART2->SR & USART_SR_TXE));
        USART2->DR = *str++;
    }
}

static void uart_send_int(int32_t val) {
    char buf[12];
    int i = 0, neg = 0;
    if (val < 0) { neg = 1; val = -val; }
    if (val == 0) buf[i++] = '0';
    else while (val > 0) { buf[i++] = '0' + (val % 10); val /= 10; }
    if (neg) buf[i++] = '-';
    while (i > 0) { while (!(USART2->SR & USART_SR_TXE)); USART2->DR = buf[--i]; }
}

/* === Step Pulse === */
static void step_pulse(GPIO_TypeDef *port, uint8_t pin) {
    port->BSRR = (1U << pin);
    delay_cycles(2000);
    port->BSRR = (1U << (pin + 16));
    delay_cycles(2000);
}

/* === Move PAN (with limits) === */
static int32_t move_pan(int32_t steps) {
    if (steps == 0) return 0;
    uint8_t dir = (steps > 0) ? 1 : 0;
    int32_t count = (steps > 0) ? steps : -steps;
    int32_t taken = 0;

    // PAN direction inverted (motor wiring)
    if (dir) GPIOA->BSRR = (1U << (10 + 16));
    else     GPIOA->BSRR = (1U << 10);
    delay_cycles(10000);

    for (int32_t i = 0; i < count; i++) {
        // Hardware limits
        if (read_pan_neg() && !dir) break;  // Hit negative limit going negative
        if (read_pan_pos() && dir) break;   // Hit positive limit going positive
        // Software limits
        int32_t next = pan_position + (dir ? 1 : -1);
        if (next < PAN_LIMIT_MIN || next > PAN_LIMIT_MAX) break;
        step_pulse(GPIOB, 3);
        pan_position = next;
        taken++;
    }
    return dir ? taken : -taken;
}

/* === Move TILT (with limits) === */
static int32_t move_tilt(int32_t steps) {
    if (steps == 0) return 0;
    uint8_t dir = (steps > 0) ? 1 : 0;
    int32_t count = (steps > 0) ? steps : -steps;
    int32_t taken = 0;

    if (dir) GPIOB->BSRR = (1U << 5);
    else     GPIOB->BSRR = (1U << (5 + 16));
    delay_cycles(10000);

    for (int32_t i = 0; i < count; i++) {
        // Hardware limits
        if (read_tilt_neg() && !dir) break;  // Hit negative limit going negative
        if (read_tilt_pos() && dir) break;   // Hit positive limit going positive
        // Software limits
        int32_t next = tilt_position + (dir ? 1 : -1);
        if (next < TILT_LIMIT_MIN || next > TILT_LIMIT_MAX) break;
        step_pulse(GPIOB, 4);
        tilt_position = next;
        taken++;
    }
    return dir ? taken : -taken;
}

/* === Homing (moves to negative limit, then to center) === */
static void home_pan(void) {
    uart_send_str("HOMING PAN...\r\n");
    // PAN direction inverted (motor wiring)
    GPIOA->BSRR = (1U << 10);  // DIR toward negative limit (inverted)
    delay_cycles(10000);

    uint32_t count = 0;
    while (!read_pan_neg() && count < 20000) { step_pulse(GPIOB, 3); count++; }
    if (count >= 20000) { uart_send_str("ERROR: PAN NEG LIMIT NOT FOUND\r\n"); return; }

    delay_cycles(100000);
    GPIOA->BSRR = (1U << (10 + 16));  // Back off (inverted)
    for (int i = 0; i < 200; i++) step_pulse(GPIOB, 3);

    delay_cycles(100000);
    GPIOA->BSRR = (1U << 10);  // Slow approach (inverted)
    while (!read_pan_neg()) { step_pulse(GPIOB, 3); delay_cycles(5000); }

    pan_position = 0;  // Home position is 0 (at left limit switch)
    pan_homed = 1;
    uart_send_str("PAN HOMED\r\n");
}

static void home_tilt(void) {
    uart_send_str("HOMING TILT...\r\n");
    GPIOB->BSRR = (1U << (5 + 16));  // DIR toward negative limit
    delay_cycles(10000);

    uint32_t count = 0;
    while (!read_tilt_neg() && count < 5000) { step_pulse(GPIOB, 4); count++; }
    if (count >= 5000) { uart_send_str("ERROR: TILT NEG LIMIT NOT FOUND\r\n"); return; }

    delay_cycles(100000);
    GPIOB->BSRR = (1U << 5);  // Back off
    for (int i = 0; i < 200; i++) step_pulse(GPIOB, 4);

    delay_cycles(100000);
    GPIOB->BSRR = (1U << (5 + 16));  // Slow approach
    while (!read_tilt_neg()) { step_pulse(GPIOB, 4); delay_cycles(5000); }

    tilt_position = TILT_LIMIT_MIN;  // Now at negative limit
    tilt_homed = 1;
    uart_send_str("TILT HOMED\r\n");
}

/* === Main === */
int main(void) {
    gpio_init();
    usart2_init();

    // Microstep 1/8: M2=0, M1=1, M0=1
    M2_PORT->BSRR = (1U << (M2_PIN + 16));
    M1_PORT->BSRR = (1U << M1_PIN);
    M0_PORT->BSRR = (1U << M0_PIN);

    uart_send_str("READY\r\n");

    while (1) {
        if (!cmd_ready) continue;

        int32_t steps, actual;

        // Relative motion
        if (strncmp((char*)rx_buffer, "PAN_REL:", 8) == 0) {
            steps = atoi((char*)rx_buffer + 8);
            actual = move_pan(steps);
            uart_send_str("OK PAN:"); uart_send_int(actual); uart_send_str("\r\n");
        }
        else if (strncmp((char*)rx_buffer, "TILT_REL:", 9) == 0) {
            steps = atoi((char*)rx_buffer + 9);
            actual = move_tilt(steps);
            uart_send_str("OK TILT:"); uart_send_int(actual); uart_send_str("\r\n");
        }
        // Absolute motion
        else if (strncmp((char*)rx_buffer, "PAN_ABS:", 8) == 0) {
            int32_t target = atoi((char*)rx_buffer + 8);
            move_pan(target - pan_position);
            uart_send_str("OK PAN:"); uart_send_int(pan_position); uart_send_str("\r\n");
        }
        else if (strncmp((char*)rx_buffer, "TILT_ABS:", 9) == 0) {
            int32_t target = atoi((char*)rx_buffer + 9);
            move_tilt(target - tilt_position);
            uart_send_str("OK TILT:"); uart_send_int(tilt_position); uart_send_str("\r\n");
        }
        // Homing
        else if (strcmp((char*)rx_buffer, "HOME_PAN") == 0) { home_pan(); }
        else if (strcmp((char*)rx_buffer, "HOME_TILT") == 0) { home_tilt(); }
        else if (strcmp((char*)rx_buffer, "HOME_ALL") == 0) {
            home_pan(); home_tilt(); uart_send_str("ALL HOMED\r\n");
        }
        // Center (move to 0,0)
        else if (strcmp((char*)rx_buffer, "CENTER") == 0) {
            move_pan(-pan_position);
            move_tilt(-tilt_position);
            uart_send_str("CENTERED\r\n");
        }
        // Status
        else if (strcmp((char*)rx_buffer, "GET_POS") == 0) {
            uart_send_str("POS PAN:"); uart_send_int(pan_position);
            uart_send_str(" TILT:"); uart_send_int(tilt_position); uart_send_str("\r\n");
        }
        else if (strcmp((char*)rx_buffer, "GET_STATUS") == 0) {
            uart_send_str("STATUS PN:"); uart_send_int(read_pan_neg());
            uart_send_str(" PP:"); uart_send_int(read_pan_pos());
            uart_send_str(" TN:"); uart_send_int(read_tilt_neg());
            uart_send_str(" TP:"); uart_send_int(read_tilt_pos());
            uart_send_str(" PH:"); uart_send_int(pan_homed);
            uart_send_str(" TH:"); uart_send_int(tilt_homed); uart_send_str("\r\n");
        }
        else if (strcmp((char*)rx_buffer, "PING") == 0) { uart_send_str("PONG\r\n"); }
        // Unknown
        else { uart_send_str("ERROR:"); uart_send_str((char*)rx_buffer); uart_send_str("\r\n"); }

        cmd_ready = 0;
    }
}
