/**
 * Minimal STM32F4xx device header for bare-metal stepper + UART control
 * Subset of CMSIS definitions for STM32F401RETx
 */

#ifndef STM32F4XX_H
#define STM32F4XX_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>

/* ============================================================
 * Cortex-M4 Core Intrinsics
 * ============================================================ */

#define __NOP()  __asm volatile ("nop")
#define __DSB()  __asm volatile ("dsb 0xF":::"memory")
#define __ISB()  __asm volatile ("isb 0xF":::"memory")
#define __WFI()  __asm volatile ("wfi")

/* ============================================================
 * NVIC (Nested Vectored Interrupt Controller)
 * ============================================================ */

#define NVIC_ISER_BASE  0xE000E100UL
#define NVIC_ICER_BASE  0xE000E180UL

typedef struct {
    volatile uint32_t ISER[8];   /* Interrupt Set Enable */
    uint32_t RESERVED0[24];
    volatile uint32_t ICER[8];   /* Interrupt Clear Enable */
    uint32_t RESERVED1[24];
    volatile uint32_t ISPR[8];   /* Interrupt Set Pending */
    uint32_t RESERVED2[24];
    volatile uint32_t ICPR[8];   /* Interrupt Clear Pending */
    uint32_t RESERVED3[24];
    volatile uint32_t IABR[8];   /* Interrupt Active Bit */
    uint32_t RESERVED4[56];
    volatile uint8_t  IP[240];   /* Interrupt Priority */
} NVIC_Type;

#define NVIC  ((NVIC_Type *) 0xE000E100UL)

/* IRQ Numbers for STM32F401 */
typedef enum {
    USART1_IRQn = 37,
    USART2_IRQn = 38,
    USART6_IRQn = 71
} IRQn_Type;

static inline void NVIC_EnableIRQ(IRQn_Type IRQn) {
    NVIC->ISER[(uint32_t)IRQn >> 5] = (1UL << ((uint32_t)IRQn & 0x1F));
}

static inline void NVIC_DisableIRQ(IRQn_Type IRQn) {
    NVIC->ICER[(uint32_t)IRQn >> 5] = (1UL << ((uint32_t)IRQn & 0x1F));
}

/* ============================================================
 * Memory Map - Peripheral Base Addresses
 * ============================================================ */

#define PERIPH_BASE       0x40000000UL
#define APB1PERIPH_BASE   PERIPH_BASE
#define APB2PERIPH_BASE   (PERIPH_BASE + 0x00010000UL)
#define AHB1PERIPH_BASE   (PERIPH_BASE + 0x00020000UL)

/* GPIO base addresses (AHB1) */
#define GPIOA_BASE        (AHB1PERIPH_BASE + 0x0000UL)
#define GPIOB_BASE        (AHB1PERIPH_BASE + 0x0400UL)
#define GPIOC_BASE        (AHB1PERIPH_BASE + 0x0800UL)
#define GPIOD_BASE        (AHB1PERIPH_BASE + 0x0C00UL)

/* RCC base address (AHB1) */
#define RCC_BASE          (AHB1PERIPH_BASE + 0x3800UL)

/* USART base addresses */
#define USART2_BASE       (APB1PERIPH_BASE + 0x4400UL)
#define USART1_BASE       (APB2PERIPH_BASE + 0x1000UL)
#define USART6_BASE       (APB2PERIPH_BASE + 0x1400UL)

/* ============================================================
 * GPIO Register Structure
 * ============================================================ */

typedef struct {
    volatile uint32_t MODER;    /* Mode register                 - offset 0x00 */
    volatile uint32_t OTYPER;   /* Output type register          - offset 0x04 */
    volatile uint32_t OSPEEDR;  /* Output speed register         - offset 0x08 */
    volatile uint32_t PUPDR;    /* Pull-up/pull-down register    - offset 0x0C */
    volatile uint32_t IDR;      /* Input data register           - offset 0x10 */
    volatile uint32_t ODR;      /* Output data register          - offset 0x14 */
    volatile uint32_t BSRR;     /* Bit set/reset register        - offset 0x18 */
    volatile uint32_t LCKR;     /* Lock register                 - offset 0x1C */
    volatile uint32_t AFR[2];   /* Alternate function registers  - offset 0x20-0x24 */
} GPIO_TypeDef;

/* ============================================================
 * RCC Register Structure
 * ============================================================ */

typedef struct {
    volatile uint32_t CR;            /* offset 0x00 */
    volatile uint32_t PLLCFGR;       /* offset 0x04 */
    volatile uint32_t CFGR;          /* offset 0x08 */
    volatile uint32_t CIR;           /* offset 0x0C */
    volatile uint32_t AHB1RSTR;      /* offset 0x10 */
    volatile uint32_t AHB2RSTR;      /* offset 0x14 */
    uint32_t RESERVED0[2];           /* offset 0x18-0x1C */
    volatile uint32_t APB1RSTR;      /* offset 0x20 */
    volatile uint32_t APB2RSTR;      /* offset 0x24 */
    uint32_t RESERVED1[2];           /* offset 0x28-0x2C */
    volatile uint32_t AHB1ENR;       /* offset 0x30 */
    volatile uint32_t AHB2ENR;       /* offset 0x34 */
    uint32_t RESERVED2[2];           /* offset 0x38-0x3C */
    volatile uint32_t APB1ENR;       /* offset 0x40 */
    volatile uint32_t APB2ENR;       /* offset 0x44 */
} RCC_TypeDef;

/* ============================================================
 * USART Register Structure
 * ============================================================ */

typedef struct {
    volatile uint32_t SR;    /* Status register         - offset 0x00 */
    volatile uint32_t DR;    /* Data register           - offset 0x04 */
    volatile uint32_t BRR;   /* Baud rate register      - offset 0x08 */
    volatile uint32_t CR1;   /* Control register 1      - offset 0x0C */
    volatile uint32_t CR2;   /* Control register 2      - offset 0x10 */
    volatile uint32_t CR3;   /* Control register 3      - offset 0x14 */
    volatile uint32_t GTPR;  /* Guard time/prescaler    - offset 0x18 */
} USART_TypeDef;

/* ============================================================
 * Peripheral Declarations
 * ============================================================ */

#define GPIOA   ((GPIO_TypeDef *)  GPIOA_BASE)
#define GPIOB   ((GPIO_TypeDef *)  GPIOB_BASE)
#define GPIOC   ((GPIO_TypeDef *)  GPIOC_BASE)
#define GPIOD   ((GPIO_TypeDef *)  GPIOD_BASE)
#define RCC     ((RCC_TypeDef *)   RCC_BASE)
#define USART1  ((USART_TypeDef *) USART1_BASE)
#define USART2  ((USART_TypeDef *) USART2_BASE)
#define USART6  ((USART_TypeDef *) USART6_BASE)

/* ============================================================
 * RCC Bit Definitions
 * ============================================================ */

/* AHB1ENR - GPIO clock enables */
#define RCC_AHB1ENR_GPIOAEN     (1U << 0)
#define RCC_AHB1ENR_GPIOBEN     (1U << 1)
#define RCC_AHB1ENR_GPIOCEN     (1U << 2)
#define RCC_AHB1ENR_GPIODEN     (1U << 3)

/* APB1ENR - Peripheral clock enables */
#define RCC_APB1ENR_USART2EN    (1U << 17)
#define RCC_APB1ENR_TIM2EN      (1U << 0)
#define RCC_APB1ENR_TIM3EN      (1U << 1)

/* APB2ENR - Peripheral clock enables */
#define RCC_APB2ENR_USART1EN    (1U << 4)
#define RCC_APB2ENR_USART6EN    (1U << 5)

/* ============================================================
 * USART Bit Definitions
 * ============================================================ */

/* USART_SR - Status Register */
#define USART_SR_PE       (1U << 0)   /* Parity error */
#define USART_SR_FE       (1U << 1)   /* Framing error */
#define USART_SR_NE       (1U << 2)   /* Noise error */
#define USART_SR_ORE      (1U << 3)   /* Overrun error */
#define USART_SR_IDLE     (1U << 4)   /* Idle line detected */
#define USART_SR_RXNE     (1U << 5)   /* Read data register not empty */
#define USART_SR_TC       (1U << 6)   /* Transmission complete */
#define USART_SR_TXE      (1U << 7)   /* Transmit data register empty */

/* USART_CR1 - Control Register 1 */
#define USART_CR1_SBK     (1U << 0)   /* Send break */
#define USART_CR1_RWU     (1U << 1)   /* Receiver wakeup */
#define USART_CR1_RE      (1U << 2)   /* Receiver enable */
#define USART_CR1_TE      (1U << 3)   /* Transmitter enable */
#define USART_CR1_IDLEIE  (1U << 4)   /* IDLE interrupt enable */
#define USART_CR1_RXNEIE  (1U << 5)   /* RXNE interrupt enable */
#define USART_CR1_TCIE    (1U << 6)   /* Transmission complete interrupt enable */
#define USART_CR1_TXEIE   (1U << 7)   /* TXE interrupt enable */
#define USART_CR1_PEIE    (1U << 8)   /* PE interrupt enable */
#define USART_CR1_PS      (1U << 9)   /* Parity selection */
#define USART_CR1_PCE     (1U << 10)  /* Parity control enable */
#define USART_CR1_WAKE    (1U << 11)  /* Wakeup method */
#define USART_CR1_M       (1U << 12)  /* Word length */
#define USART_CR1_UE      (1U << 13)  /* USART enable */
#define USART_CR1_OVER8   (1U << 15)  /* Oversampling mode */

/* ============================================================
 * System Initialization
 * ============================================================ */

static inline void SystemInit(void) {
    /* Default clock is HSI at 16 MHz */
}

#ifdef __cplusplus
}
#endif

#endif /* STM32F4XX_H */
