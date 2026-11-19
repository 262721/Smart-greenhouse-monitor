from machine import I2C, Pin, ADC, Timer
from sh1106 import SH1106_I2C
import time
from bme280 import BME280
from gpio_devices import Button
import neopixel


# Settings I might want to change later

# Limits for the targets so they can't go crazy
TEMP_MIN, TEMP_MAX = 10, 35
HUM_MIN, HUM_MAX = 20, 80

# Joystick limits (the ADC gives 0 - 65535, the middle is about 32768).
# Above JOY_HIGH = pushed one way, below JOY_LOW = pushed the other way.
# They have to be about the same distance from the middle, and not too close
# to 0 or 65535, because the stick often does not reach the very ends.
# I had 450 for the low one and subtracting almost never worked.
JOY_HIGH = 47000
JOY_LOW = 18000

# The joystick is read every 100 ms (timer). When I hold the stick,
# the target changes every STEP_EVERY reads.
# 3 = one step per 300 ms (easy to control), 1 = one step per 100 ms (fast)
STEP_EVERY = 3

# The joystick button goes through these modes: which target and how big
# the step is. The X axis then changes that target by that step.
MODES = (
    ("temp", 1),
    ("temp", 0.1),
    ("hum", 1),
    ("hum", 0.1),
)

# How often I read the BME280 (once per second is enough)
SENSOR_PERIOD_MS = 1000

# Button that resets the whole thing (pressed = pin goes to 0)
RESET_PIN = 17

# Relay for the heater (1 = relay on)
RELAY_PIN = 16

# Heater: switch ON when it is more than HEAT_ON_BELOW degrees under the
# target, switch OFF when it is more than HEAT_OFF_ABOVE degrees over it.
# The gap between the two stops the relay from clicking all the time.
HEAT_ON_BELOW = 2
HEAT_OFF_ABOVE = 2

# Ventilator pin (1 = fan running, 0 = stopped)
FAN_PIN = 18

# Cooling is the heater turned around: fan ON when it is more than
# COOL_ON_ABOVE over the target, fan OFF when it is more than
# COOL_OFF_BELOW under it
COOL_ON_ABOVE = 2
COOL_OFF_BELOW = 2

# NeoPixel strip: data pin and how many LEDs it has.
# The Pico cannot find out the length of the strip by itself, so this number
# is the length I give it. If it is lower than the real count, the last LEDs
# stay dark. If it is higher, nothing bad happens, the extra ones just do not
# exist. Best is to count the LEDs on the strip and put that number here
PIXEL_PIN = 2
NUM_PIXELS = 100 

# The 4 colors as (R, G, B). 40 out of 255 is bright enough without
# hurting my eyes
LEVEL = 40
COLORS = (
    (LEVEL, LEVEL, LEVEL),   # white
    (0, 0, LEVEL),           # blue
    (LEVEL, 0, 0),           # red
    (0, LEVEL, 0),           # green
)


# The timer sets this flag, the main loop clears it
sample_due = False


def read_adc(adc, samples=8):
    # Take a few readings and average them, the ADC is a bit noisy
    total = 0
    for _ in range(samples):
        total += adc.read_u16()

    average = round(total / samples)
    voltage = average * 3.3 / 65_535
    return average, voltage


def clamp(value, low, high):
    # Keep the value between low and high
    return max(low, min(high, value))


def update_heater(heating, temperature, target_temp):
    # Two different thresholds (hysteresis), so the relay doesn't click on
    # and off all the time when the temperature is near the target.
    # In between I just keep whatever state it already had.
    if not heating and temperature < target_temp - HEAT_ON_BELOW:
        return True
    if heating and temperature > target_temp + HEAT_OFF_ABOVE:
        return False
    return heating


def update_cooler(cooling, temperature, target_temp):
    # Same as the heater but the other way around: too warm = fan ON.
    # In between I keep the old state again.
    if not cooling and temperature > target_temp + COOL_ON_ABOVE:
        return True
    if cooling and temperature < target_temp - COOL_OFF_BELOW:
        return False
    return cooling


def set_all_pixels(pixel, color):
    # Go through the whole length of the strip (len(pixel) is the NUM_PIXELS I
    # gave it), give every LED the same color and send it to the strip once
    for i in range(len(pixel)):
        pixel[i] = color
    pixel.write()


def draw_measurement(oled, label, value, unit, x, y):
    # Label on the left, the value 50 pixels further to the right
    oled.text(label, x, y)
    oled.text(f"{value:.1f}{unit}", x + 50, y)


def draw_screen(oled, temperature, humidity, target_temp, target_hum, selected,
                heating, cooling):
    # Draw everything into the buffer first, then send it to the display once
    oled.fill(0)

    oled.text("CURRENT", 0, 0)
    # HEAT or COOL in the top right corner (never both at the same time)
    if heating:
        oled.text("HEAT", 96, 0)
    elif cooling:
        oled.text("COOL", 96, 0)
    oled.line(0, 8, 127, 8, 1)
    draw_measurement(oled, "Temp", temperature, "C", 10, 10)
    draw_measurement(oled, "Humi", humidity, "%", 10, 20)
    oled.line(0, 28, 127, 28, 1)

    oled.text("TARGET", 0, 30)
    oled.line(0, 38, 127, 38, 1)
    draw_measurement(oled, "Temp", target_temp, "C", 10, 40)
    draw_measurement(oled, "Humi", target_hum, "%", 10, 50)
    oled.line(0, 58, 127, 58, 1)

    # The > shows which target the joystick is changing right now,
    # and the step size (+1 or +.1) goes at the end of that row
    field, step = MODES[selected]
    row_y = 40 if field == "temp" else 50
    oled.text(">", 0, row_y)
    oled.text("+1" if step >= 1 else "+.1", 104, row_y)

    oled.show()


def timer_handler(timer):
    # Keep this short, just set the flag and leave
    global sample_due
    sample_due = True


def main():
    global sample_due
    sample_due = False   # in case the timer set it before a restart

    target_temp = 20
    target_hum = 32
    selected = 0        # index in MODES (which target and which step size)
    hold_count = 0      # counts the reads while the stick is held
    color_index = 0     # which color from COLORS is on (0 = white)
    y_ready = True      # True when the stick is back in the middle

    # 400 kHz, the OLED updates about 4x faster than with 100 kHz
    i2c = I2C(0, sda=Pin(4), scl=Pin(5), freq=400_000)
    oled = SH1106_I2C(i2c)
    sensor = BME280(i2c, addr=0x76)
    adc_x = ADC(27)
    adc_y = ADC(28)     # the Y axis is for the NeoPixel color
    buttonjoy = Button(22)
    button_reset = Button(RESET_PIN)

    # All the pixels get the same color, I start with white
    pixel = neopixel.NeoPixel(Pin(PIXEL_PIN), NUM_PIXELS)
    set_all_pixels(pixel, COLORS[color_index])

    # The relay starts OFF so the heater never turns on by accident at boot
    relay = Pin(RELAY_PIN, Pin.OUT)
    relay.value(0)
    heating = False

    # The fan starts OFF too
    fan = Pin(FAN_PIN, Pin.OUT)
    fan.value(0)
    cooling = False

    timer = Timer(-1)
    timer.init(
        period=100,
        mode=Timer.PERIODIC,
        callback=timer_handler
    )

    # Read the sensor once before the loop, so I have values for the first redraw
    temperature = sensor.temperature()
    humidity = sensor.humidity()
    last_sensor_read = time.ticks_ms()

    needs_redraw = True
    restart = False     # becomes True when the reset button is pressed

    try:
        while True:
            # Reset button: leave the loop, the finally block switches everything
            # off and main() returns True, so the program starts again from the top.
            # I don't use machine.reset() because after a real reboot the program
            # only starts again if it is saved on the Pico as main.py
            if button_reset.was_pressed():
                oled.fill(0)
                oled.text("Resetting...", 16, 28)
                oled.show()
                time.sleep_ms(500)   # so I can actually see the message
                restart = True
                break

            # Joystick button: go to the next mode (temp 1, temp 0.1, hum 1, hum 0.1)
            if buttonjoy.was_pressed():
                selected = (selected + 1) % len(MODES)   # back to 0 after the last one
                needs_redraw = True

            # Joystick: only when there is a new ADC read (every 100 ms)
            if sample_due:
                sample_due = False
                raw_x, voltage_x = read_adc(adc_x)

                direction = 0
                if raw_x >= JOY_HIGH:
                    direction = 1
                elif raw_x <= JOY_LOW:
                    direction = -1

                if direction == 0:
                    # Stick is in the middle, so the next push reacts right away
                    hold_count = 0
                else:
                    # Step on the first read, then every STEP_EVERY reads
                    if hold_count == 0:
                        field, step = MODES[selected]
                        # round() gets rid of float errors like 20.200000000000003
                        if field == "temp":
                            target_temp = round(clamp(target_temp + direction * step, TEMP_MIN, TEMP_MAX), 1)
                        else:
                            target_hum = round(clamp(target_hum + direction * step, HUM_MIN, HUM_MAX), 1)
                        needs_redraw = True
                    hold_count = (hold_count + 1) % STEP_EVERY

                # Y axis: push up or down = next or previous color.
                # One change per push, the stick has to go back to the
                # middle before the next one (so the colors don't spin like crazy)
                raw_y, voltage_y = read_adc(adc_y)

                y_dir = 0
                if raw_y >= JOY_HIGH:
                    y_dir = 1
                elif raw_y <= JOY_LOW:
                    y_dir = -1

                if y_dir == 0:
                    y_ready = True
                elif y_ready:
                    y_ready = False
                    # the % makes it wrap around, after the last color comes the first
                    color_index = (color_index + y_dir) % len(COLORS)
                    set_all_pixels(pixel, COLORS[color_index])

            # Sensor: only about once per second
            if time.ticks_diff(time.ticks_ms(), last_sensor_read) >= SENSOR_PERIOD_MS:
                last_sensor_read = time.ticks_ms()
                # Temperature has to be read first, the humidity needs it
                temperature = sensor.temperature()
                humidity = sensor.humidity()
                needs_redraw = True

            # Heater: decide from the latest temperature and target.
            # This runs every pass, so changing the target reacts right away
            new_heating = update_heater(heating, temperature, target_temp)
            if new_heating != heating:
                heating = new_heating
                relay.value(1 if heating else 0)
                needs_redraw = True

            # Cooling: same idea, but reversed
            new_cooling = update_cooler(cooling, temperature, target_temp)
            if new_cooling != cooling:
                cooling = new_cooling
                fan.value(1 if cooling else 0)
                needs_redraw = True

            # Display: only redraw when something changed
            if needs_redraw:
                needs_redraw = False
                draw_screen(oled, temperature, humidity,
                            target_temp, target_hum, selected, heating, cooling)

            time.sleep_ms(5)

    except KeyboardInterrupt:
        oled.fill(0)
        oled.show()

    finally:
        # No matter what happens (Ctrl+C, a crash or the reset button), never leave
        # the heater or fan on. The timer has to be stopped too, otherwise every
        # restart would add one more timer
        timer.deinit()
        relay.value(0)
        fan.value(0)
        set_all_pixels(pixel, (0, 0, 0))   # NeoPixels off

    # True = the reset button was pressed, so the program starts again
    return restart


if __name__ == "__main__":
    # main() gives back True when the reset button was pressed, then it just
    # runs again from the beginning. Ctrl+C or a crash ends the program
    while main():
        pass
