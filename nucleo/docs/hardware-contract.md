1. Boards and Power
	•	MCU board: ST NUCLEO-F401RE (STM32F401RET6, Nucleo board, Arduino-style D pins).
	•	Motor drivers: 2 × Pololu DRV8825 breakout boards.
	•	Motors:
	•	PAN: Tevo NEMA17, model 17HD4401-style (≈1.7 A/phase), Vref set to 0.60 V on its DRV8825.
	•	TILT: Moons’ MS17HD5P4100 (1.0 A/phase), Vref set to 0.50 V on its DRV8825.

Power rails
	•	Motor supply: ~18 V laptop brick → both DRV8825 VMOT pins (in parallel).
	•	Logic supply: Nucleo 3.3 V rail (from USB) → DRV8825 logic pins (RST/SLP, optionally EN).
	•	Ground:
	•	DRV8825 GND (both boards)
	•	Nucleo GND
	•	18 V PSU negative
are all tied together (single common ground bus on breadboard).

Bulk caps
	•	Each DRV8825 board has its own 1000 µF electrolytic capacitor wired:
	•	+ → VMOT pin on that DRV8825
	•	– → nearest GND pin on that DRV8825

⸻

2. Driver ↔ MCU GPIO Mapping (NUCLEO-F401RE)

2.1 STEP / DIR for each motor

All MCU pins are 3.3 V logic outputs.
	•	PAN motor driver (Tevo, Vref 0.60 V)
	•	DIR → Nucleo D2 = PA10
	•	STEP → Nucleo D3 = PB3
	•	TILT motor driver (Moons, Vref 0.50 V)
	•	DIR → Nucleo D4 = PB5
	•	STEP → Nucleo D5 = PB4

2.2 Microstepping pins (shared by both DRV8825 boards)

All three M-pins on each DRV8825 are tied together and then to the Nucleo:
	•	M2 → Nucleo D8 = PA9
	•	M1 → Nucleo D9 = PC7
	•	M0 → Nucleo D10 = PB6

These are outputs from the STM32.

Microstep mode truth table used:

Mode	M2 (D8)	M1 (D9)	M0 (D10)
Full	0	0	0
1/2	0	0	1
1/4	0	1	0
1/8	0	1	1
1/16	1	0	0
1/32	1	0	1

Current default mode in firmware: 1/8 step → M2=0, M1=1, M0=1.

⸻

3. Driver Control Pins / Logic Levels

For both DRV8825 boards:
	•	RST and SLP are hard-wired together and tied to Nucleo 3.3 V → permanently HIGH.
	•	EN is left floating or tied to GND (board enabled):
	•	EN LOW or unconnected = driver enabled.
	•	EN HIGH = outputs disabled (not used yet in code).

Logic conventions in firmware:
	•	DIR pins (PA10, PB5):
	•	0 = “reverse”
	•	1 = “forward”
(Actual physical direction is determined by how A/B coils were wired; if it’s wrong we flip one coil per motor.)
	•	STEP pins (PB3, PB4):
	•	Each low→high→low pulse = one microstep.

No separate ENABLE GPIO is used in current code; drivers are always enabled when powered.

⸻

4. Motor ↔ Driver Coil Wiring

For each motor, coils are:
	•	Coil A (the “A01 pair” you identified with low resistance) → DRV8825 A1 and A2.
	•	Coil B (the other low-resistance pair) → DRV8825 B1 and B2.

On both motors, the A-coil wires occupy the same relative connector positions, so the wiring pattern is the same between PAN and TILT.

Important behavioral rule:
	•	To reverse direction for a given motor later, you can either:
	•	Swap the two wires on A1/A2 or B1/B2, or
	•	Invert the meaning of DIR in firmware.

⸻

5. Electrical Settings

5.1 Current limits (Vref)

Using Pololu DRV8825 formula: I_limit ≈ 2 × Vref
	•	PAN motor (Tevo 17HD4401-type)
	•	Vref set to 0.60 V
	•	I_limit ≈ 1.2 A/phase
	•	TILT motor (Moons MS17HD5P4100)
	•	Vref set to 0.50 V
	•	I_limit ≈ 1.0 A/phase

Both are within DRV8825 continuous rating (1.5 A) with reasonable cooling and within motor specs.

5.2 Power-up sequence used
	•	Plug Nucleo USB in → 3.3 V logic comes up first (RST/SLP high).
	•	Then turn on 18 V PSU for VMOT.
	•	Firmware generates STEP pulses only after everything is up.

⸻

6. Firmware-Relevant Facts (so main.c can be rebuilt)
	•	No HAL / CubeMX: we use bare-metal register access:
	•	Enable clocks via RCC->AHB1ENR.
	•	Configure GPIO as outputs via GPIOx->MODER.
	•	Set/reset pins via GPIOx->BSRR.
	•	Delay timing:
	•	Simple busy-loop delay_cycles() used for STEP pulse width and inter-step delays.
	•	No timers or interrupts used yet.
	•	Basic motor API behavior:
	•	move_motor(dir_port, dir_pin, step_port, step_pin, dir_level, steps)
	•	dir_level 0/1 sets DIR pin.
	•	Loops steps times, calling step_pulse().
	•	Microstepping:
	•	ms_pins_init() sets PA9, PB6, PC7 as outputs and defaults them to low (full-step) until set_microstep(...) is called.
	•	set_microstep(m2, m1, m0) directly drives the M2/M1/M0 pins according to the table.
