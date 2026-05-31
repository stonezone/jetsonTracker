#if !defined(STM32F446xx)
#define STM32F446xx
#endif

#include "stm32f4xx.h"
#include <string.h>
#include <stdlib.h>

/* === Pin Mapping ===
 * Motor 1 (PAN):  DIR = PB5 (D4), STEP = PB3 (D3)
 * Motor 2 (TILT): DIR = PB4 (D5), STEP = PA5 (D13)
 * Microsteps:     M2=PA9 (D8), M1=PA2 (D1), M0=PB6 (D10)
 * Limit Switches: PAN_NEG=PA7 (D11), TILT_NEG=PA8 (D7)
 *                 PAN_POS=PA10 (D2), TILT_POS=PA6 (D12)
 * USART6:         TX=PC6 (CN10-4), RX=PC7 (D9)
 * NOTE: D6/PB10 shorted to GND on this board, using D13 for TILT_STEP
 */

#define M0_PORT GPIOB
#define M0_PIN  6
#define M1_PORT GPIOA
#define M1_PIN  2      // Moved from PC7 to PA2 (D1) to free PC7 for USART6
#define M2_PORT GPIOA
#define M2_PIN  9

/* Limit switches - negative direction (home) - D11/PA7 stops leftward motion */
#define PAN_NEG_PORT    GPIOA
#define PAN_NEG_PIN     7
#define TILT_NEG_PORT   GPIOA
#define TILT_NEG_PIN    8

/* Limit switches - positive direction - D2/PA10 stops rightward motion */
#define PAN_POS_PORT    GPIOA
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

/* Software Limits (steps from center home position)
 * After homing, position is 0 at CENTER between limits.
 * PAN: Physical travel ~4255 steps, so ±2100 from center
 * TILT: Physical travel ~2675 steps, so ±1300 from center
 * Set soft limits slightly inside physical limits for safety.
 */
#define PAN_LIMIT_MIN   -2100   // Toward left limit (-90°)
#define PAN_LIMIT_MAX   2100    // Toward right limit (+90°)
#define TILT_LIMIT_MIN  -1300   // Toward down limit (-90°)
#define TILT_LIMIT_MAX  1300    // Toward up limit (+90°)

/* === Delay === */
static void delay_cycles(volatile uint32_t cycles) {
    while (cycles--) { __NOP(); }
}

/* === GPIO Init === */
static void gpio_init(void) {
    RCC->AHB1ENR |= RCC_AHB1ENR_GPIOAEN | RCC_AHB1ENR_GPIOBEN | RCC_AHB1ENR_GPIOCEN;

    // Motor DIR/STEP outputs: PAN(D4=PB5,D3=PB3) TILT(D5=PB4,D13=PA5)
    GPIOB->MODER &= ~((3U << (3*2)) | (3U << (4*2)) | (3U << (5*2)));
    GPIOB->MODER |=  ((1U << (3*2)) | (1U << (4*2)) | (1U << (5*2)));
    GPIOA->MODER &= ~(3U << (5*2));
    GPIOA->MODER |=  (1U << (5*2));  // PA5 (D13) = TILT_STEP

    // Microstep outputs
    M0_PORT->MODER &= ~(3U << (M0_PIN * 2)); M0_PORT->MODER |= (1U << (M0_PIN * 2));
    M1_PORT->MODER &= ~(3U << (M1_PIN * 2)); M1_PORT->MODER |= (1U << (M1_PIN * 2));
    M2_PORT->MODER &= ~(3U << (M2_PIN * 2)); M2_PORT->MODER |= (1U << (M2_PIN * 2));

    // USART6 (PC6=TX, PC7=RX) -> AF8
    GPIOC->MODER &= ~((3U << (6*2)) | (3U << (7*2)));
    GPIOC->MODER |=  ((2U << (6*2)) | (2U << (7*2)));
    GPIOC->AFR[0] |= (8U << (6*4)) | (8U << (7*4));

    // Limit switch inputs with pull-ups (active-low)
    // PAN negative limit (D11 = PA7)
    PAN_NEG_PORT->MODER &= ~(3U << (PAN_NEG_PIN * 2));
    PAN_NEG_PORT->PUPDR &= ~(3U << (PAN_NEG_PIN * 2));
    PAN_NEG_PORT->PUPDR |=  (1U << (PAN_NEG_PIN * 2));

    // TILT negative limit (D7 = PA8)
    TILT_NEG_PORT->MODER &= ~(3U << (TILT_NEG_PIN * 2));
    TILT_NEG_PORT->PUPDR &= ~(3U << (TILT_NEG_PIN * 2));
    TILT_NEG_PORT->PUPDR |=  (1U << (TILT_NEG_PIN * 2));

    // PAN positive limit (D2 = PA10)
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

/* === USART6 Init (115200 8N1 @ 16MHz HSI) === */
static void usart6_init(void) {
    RCC->APB2ENR |= RCC_APB2ENR_USART6EN;
    USART6->BRR = 0x8B;  // 16MHz / 115200
    USART6->CR1 |= USART_CR1_RE | USART_CR1_TE | USART_CR1_RXNEIE;
    USART6->CR1 |= USART_CR1_UE;
    NVIC_EnableIRQ(USART6_IRQn);
}

/* === USART6 IRQ Handler === */
void USART6_IRQHandler(void) {
    if (USART6->SR & USART_SR_RXNE) {
        char c = USART6->DR;
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
        while (!(USART6->SR & USART_SR_TXE));
        USART6->DR = *str++;
    }
}

static void uart_send_int(int32_t val) {
    char buf[12];
    int i = 0, neg = 0;
    if (val < 0) { neg = 1; val = -val; }
    if (val == 0) buf[i++] = '0';
    else while (val > 0) { buf[i++] = '0' + (val % 10); val /= 10; }
    if (neg) buf[i++] = '-';
    while (i > 0) { while (!(USART6->SR & USART_SR_TXE)); USART6->DR = buf[--i]; }
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

    // PAN direction: PB5 (D4)
    if (dir) GPIOB->BSRR = (1U << (5 + 16));
    else     GPIOB->BSRR = (1U << 5);
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

    // TILT direction: PB4 (D5)
    if (dir) GPIOB->BSRR = (1U << 4);
    else     GPIOB->BSRR = (1U << (4 + 16));
    delay_cycles(10000);

    for (int32_t i = 0; i < count; i++) {
        // Hardware limits
        if (read_tilt_neg() && !dir) break;  // Hit negative limit going negative
        if (read_tilt_pos() && dir) break;   // Hit positive limit going positive
        // Software limits
        int32_t next = tilt_position + (dir ? 1 : -1);
        if (next < TILT_LIMIT_MIN || next > TILT_LIMIT_MAX) break;
        step_pulse(GPIOA, 5);  // TILT STEP: PA5 (D13)
        tilt_position = next;
        taken++;
    }
    return dir ? taken : -taken;
}

/* === Homing (finds center between limits) === */
static void home_pan(void) {
    uart_send_str("HOMING PAN...\r\n");
    // PAN DIR: PB5 (D4), STEP: PB3 (D3)

    // Step 1: Move to NEG limit
    GPIOB->BSRR = (1U << 5);  // DIR toward negative limit
    delay_cycles(10000);
    uint32_t count = 0;
    while (!read_pan_neg() && count < 20000) { step_pulse(GPIOB, 3); count++; }
    if (count >= 20000) { uart_send_str("ERROR: PAN NEG LIMIT NOT FOUND\r\n"); return; }

    // Back off NEG limit
    delay_cycles(100000);
    GPIOB->BSRR = (1U << (5 + 16));  // DIR toward positive
    for (int i = 0; i < 50; i++) step_pulse(GPIOB, 3);

    // Step 2: Count steps to POS limit
    delay_cycles(100000);
    uint32_t total_steps = 0;
    while (!read_pan_pos() && total_steps < 20000) { step_pulse(GPIOB, 3); total_steps++; }
    if (total_steps >= 20000) { uart_send_str("ERROR: PAN POS LIMIT NOT FOUND\r\n"); return; }

    uart_send_str("PAN RANGE: ");
    uart_send_int(total_steps);
    uart_send_str(" steps\r\n");

    // Step 3: Move to center
    uint32_t center = total_steps / 2;
    GPIOB->BSRR = (1U << 5);  // DIR toward negative (back to center)
    delay_cycles(10000);
    for (uint32_t i = 0; i < center; i++) step_pulse(GPIOB, 3);

    pan_position = 0;  // Center is 0
    pan_homed = 1;
    uart_send_str("PAN HOMED AT CENTER\r\n");
}

static void home_tilt(void) {
    uart_send_str("HOMING TILT...\r\n");
    // TILT DIR: PB4 (D5), STEP: PA5 (D13)

    // Step 1: Move to NEG limit
    GPIOB->BSRR = (1U << (4 + 16));  // DIR toward negative limit
    delay_cycles(10000);
    uint32_t count = 0;
    while (!read_tilt_neg() && count < 10000) { step_pulse(GPIOA, 5); count++; }
    if (count >= 10000) { uart_send_str("ERROR: TILT NEG LIMIT NOT FOUND\r\n"); return; }

    // Back off NEG limit
    delay_cycles(100000);
    GPIOB->BSRR = (1U << 4);  // DIR toward positive
    for (int i = 0; i < 50; i++) step_pulse(GPIOA, 5);

    // Step 2: Count steps to POS limit
    delay_cycles(100000);
    uint32_t total_steps = 0;
    while (!read_tilt_pos() && total_steps < 10000) { step_pulse(GPIOA, 5); total_steps++; }
    if (total_steps >= 10000) { uart_send_str("ERROR: TILT POS LIMIT NOT FOUND\r\n"); return; }

    uart_send_str("TILT RANGE: ");
    uart_send_int(total_steps);
    uart_send_str(" steps\r\n");

    // Step 3: Move to center
    uint32_t center = total_steps / 2;
    GPIOB->BSRR = (1U << (4 + 16));  // DIR toward negative (back to center)
    delay_cycles(10000);
    for (uint32_t i = 0; i < center; i++) step_pulse(GPIOA, 5);

    tilt_position = 0;  // Center is 0
    tilt_homed = 1;
    uart_send_str("TILT HOMED AT CENTER\r\n");
}

/* === Main === */
int main(void) {
    gpio_init();
    usart6_init();

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
