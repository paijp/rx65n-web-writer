/* Serial test for the RX65N Envision Kit over the on-board E2 OB.
 *
 * Boot mode on this part uses SCI1, and on this board TXD1 is on P26 and RXD1
 * on P30. The transmit pin was found by driving P26 and PF0 in turn, three
 * seconds each with a different message, and seeing which one the probe heard:
 * only the P26 lines ever arrived.
 *
 * Two builds:
 *   default    echo every byte straight back, to exercise both directions
 *   -DFLOOD    numbered lines back to back, so the host can look for gaps in
 *              the sequence and tell whether its reads lose anything
 *
 * -DBAUD=<n> picks the rate. The probe's own UART can only be set to 3 MHz / n
 * (see docs/rates.md), so only rates that both sides hit are useful:
 *
 *   115200   PLL, PCLKB 48 MHz, BRR 25 -> 115384.6 bps, exactly 3e6/26
 *   125000   HOCO, PCLKB 16 MHz, BRR 7 -> 125000 bps, exactly 3e6/24
 *   9600     HOCO, PCLKB 16 MHz, BRR 103 -> 9615 bps, 0.16% off 3e6/313
 */

#include "iodefine.h"

#ifndef BAUD
#define BAUD          115200
#endif

/* 115200 is not on the internal oscillator's grid - from HOCO at 16 MHz the
 * nearest rates are 125000 and 111111, both about 4% out, and SEMR.BRME with
 * MDDR did not work on SCI1 here. So that rate runs the PLL off the board's
 * external clock:
 *
 *   PLL     12 MHz * 16      = 192 MHz
 *   ICLK    192 / 2          =  96 MHz  (limit 120)
 *   PCLKB   192 / 4          =  48 MHz  (limit 60)
 *   BRR     48e6 / (16 * 26) - 1 = 25   -> 115384.6 bps
 *
 * which is the same value the probe produces at divisor 26, so the two sides
 * match exactly rather than merely closely.
 *
 * The main clock is 12 MHz on this board, whatever the programming tools are
 * told: a first attempt assumed 16 MHz and measured out at 87 kbps, which is
 * what 12 MHz gives through the same PLL settings. MAIN_CLOCK_HZ is the one
 * value to change for a board with a different crystal.
 *
 * Every other rate stays on HOCO, where PCLKB is 16 MHz and
 *   BRR = PCLKB / (16 * baud) - 1
 * with SEMR.ABCS set (16 clocks per bit rather than 32).
 */
#ifndef MAIN_CLOCK_HZ
#define MAIN_CLOCK_HZ 12000000UL
#endif

#if BAUD == 115200
#define USE_PLL       1
#define PCLKB_HZ      48000000UL
#else
#define USE_PLL       0
#define PCLKB_HZ      16000000UL
#endif

#define BRR_VALUE     ((PCLKB_HZ / (16UL * BAUD)) - 1UL)

#define PSEL_SCI1     0x0A

#if USE_PLL

/* PLLCR.STC counts in half steps, as 2 * multiplier - 1, and PLLSRCSEL 0 takes
 * the main clock. PLIDIV 00 divides the input by 1, 01 by 2.
 *
 * The encoding was confirmed the hard way: STC 47 with PLIDIV 01, meant as
 * x24 on a 16 MHz input, produced 87 kbps where 115 was wanted - exactly what
 * x24 on 12/2 MHz gives. So both fields behave as documented and the input
 * assumption was the error.
 */
#if MAIN_CLOCK_HZ == 12000000UL
#define PLL_STC       31                /* x16, PLIDIV /1: 12 * 16 = 192 MHz */
#define PLL_PLIDIV    0x0000
#elif MAIN_CLOCK_HZ == 16000000UL
#define PLL_STC       47                /* x24, PLIDIV /2: 8 * 24 = 192 MHz */
#define PLL_PLIDIV    0x0001
#else
#error "no PLL settings for this main clock; work out a 192 MHz combination"
#endif

#define PLLCR_VALUE   ((PLL_STC << 8) | PLL_PLIDIV)

/* FCK /4, ICK /2, BCK /4, PCKA /2, PCKB /4, PCKC /4, PCKD /4. The divider
 * fields encode 0 as x1, 1 as /2, 2 as /4.
 */
#define SCKCR_VALUE   0x21021222UL

static void clock_init(void)
{
    SYSTEM.PRCR.WORD = 0xA50B;          /* unlock clock registers */

    /* ICLK will be 96 MHz, which needs one ROM wait state. Set it before the
     * clock goes up, not after.
     */
    SYSTEM.ROMWT.BYTE = 0x01;

    SYSTEM.MOFCR.BYTE = 0x20;           /* resonator, 8.1 to 16 MHz drive */
    SYSTEM.MOSCWTCR.BYTE = 0x53;        /* oscillation stabilisation wait */
    SYSTEM.MOSCCR.BYTE = 0x00;          /* start the main clock */
    while (SYSTEM.OSCOVFSR.BIT.MOOVF == 0) {
    }

    SYSTEM.PLLCR.WORD = PLLCR_VALUE;
    SYSTEM.PLLCR2.BYTE = 0x00;          /* enable the PLL */
    while (SYSTEM.OSCOVFSR.BIT.PLOVF == 0) {
    }

    SYSTEM.SCKCR.LONG = SCKCR_VALUE;
    SYSTEM.SCKCR2.WORD = 0x0031;        /* UCK /4 = 48 MHz; bit 0 reads as 1 */
    SYSTEM.SCKCR3.WORD = 0x0400;        /* clock source = PLL */

    SYSTEM.PRCR.WORD = 0xA500;
}

#else

static void clock_init(void)
{
    SYSTEM.PRCR.WORD = 0xA50B;          /* unlock clock registers */

    SYSTEM.HOCOPCR.BYTE = 0x00;         /* HOCO power on */
    SYSTEM.HOCOCR.BYTE = 0x00;          /* start HOCO */
    while (SYSTEM.OSCOVFSR.BIT.HCOVF == 0) {
    }

    SYSTEM.SCKCR.LONG = 0x00000000;     /* all dividers 1:1 */
    SYSTEM.SCKCR2.WORD = 0x0011;
    SYSTEM.SCKCR3.WORD = 0x0100;        /* clock source = HOCO */

    SYSTEM.PRCR.WORD = 0xA500;
}

#endif

static void sci1_init(void)
{
    SYSTEM.PRCR.WORD = 0xA50B;
    MSTP_SCI1 = 0;                      /* release SCI1 from module stop */
    SYSTEM.PRCR.WORD = 0xA500;

    SCI1.SCR.BYTE = 0x00;
    SCI1.SMR.BYTE = 0x00;               /* 8N1, PCLK/1 */
    SCI1.BRR = (unsigned char)BRR_VALUE;
    SCI1.SEMR.BYTE = 0x10;              /* ABCS = 1: 16 clocks per bit */

    MPC.PWPR.BYTE = 0x00;               /* clear PFSWE protection */
    MPC.PWPR.BYTE = 0x40;               /* PFS writable */
    MPC.P26PFS.BYTE = PSEL_SCI1;        /* P26 = TXD1 */
    MPC.P30PFS.BYTE = PSEL_SCI1;        /* P30 = RXD1 */
    MPC.PWPR.BYTE = 0x80;
    PORT2.PMR.BIT.B6 = 1;               /* P26 driven by the peripheral */
    PORT3.PMR.BIT.B0 = 1;               /* P30 read by the peripheral */

    SCI1.SCR.BYTE = 0x30;               /* TE and RE */
}

static void put_char(char c)
{
    while (SCI1.SSR.BIT.TDRE == 0) {
    }
    SCI1.TDR = (unsigned char)c;
}

static void put_string(const char *s)
{
    while (*s) {
        put_char(*s++);
    }
}

static void put_uint(unsigned long v)
{
    char buf[11];
    int n = 0;
    if (v == 0) {
        put_char('0');
        return;
    }
    while (v && n < (int)sizeof(buf)) {
        buf[n++] = (char)('0' + (v % 10));
        v /= 10;
    }
    while (n--) {
        put_char(buf[n]);
    }
}

/* Returns -1 when nothing has arrived, -2 after clearing a receive error. The
 * error flags latch and stop reception until they are cleared.
 */
static int get_char(void)
{
    if (SCI1.SSR.BYTE & 0x38) {          /* ORER | FER | PER */
        SCI1.SSR.BYTE = (unsigned char)(SCI1.SSR.BYTE & ~0x38);
        return -2;
    }
    if (SCI1.SSR.BIT.RDRF == 0) {
        return -1;
    }
    return SCI1.RDR;
}

static void banner(const char *what)
{
    put_string("\r\nRX65N ");
    put_string(what);
    put_string(", SCI1 TXD1=P26 RXD1=P30, ");
    put_uint(BAUD);
    put_string(" bps, PCLKB ");
    put_uint(PCLKB_HZ / 1000000UL);
    put_string(" MHz");
#if USE_PLL
    put_string(" (PLL)");
#else
    put_string(" (HOCO)");
#endif
    put_string(", BRR ");
    put_uint(BRR_VALUE);
    put_string("\r\n");
}

int main(void)
{
    unsigned long n = 0;

    clock_init();
    sci1_init();

#ifdef FLOOD
    banner("flood test");
    for (;;) {
        put_string("line ");
        put_uint(n);
        put_string(" abcdefghijklmnopqrstuvwxyz\r\n");
        n++;
    }
#else
    banner("echo test");
    put_string("every byte received is sent straight back\r\n");

    for (;;) {
        int c = get_char();
        if (c == -2) {
            put_string("[receive error cleared]\r\n");
        } else if (c >= 0) {
            /* One byte out per byte in. Anything wordier overruns the receiver
             * while the reply is still going out: an earlier version printed
             * "got 0x48 'H'" per byte and lost everything after the first.
             */
            put_char((char)c);
            if (c == '\r') {
                put_char('\n');
            }
        } else if (++n >= 3000000UL) {
            /* Nothing waiting for a long time; say so, so the link is visibly
             * alive even with no input.
             */
            n = 0;
            put_string("idle\r\n");
        }
    }
#endif
}
