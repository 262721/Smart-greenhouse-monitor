from machine import I2C, Pin, ADC, Timer
from sh1106 import SH1106_I2C
import time
from bme280 import BME280
from gpio_devices import Button
import neopixel


# ---------- Settings ----------
# Limits for the target values, so they cannot run away
TEMP_MIN, TEMP_MAX = 10, 35
HUM_MIN, HUM_MAX = 20, 80

# Joystick thresholds (16-bit ADC, 0 - 65535)
JOY_HIGH = 55000   # above this = stick pushed to one side -> increase
JOY_LOW = 450      # below this = stick pushed to the other side -> decrease

# Joystick is sampled every 100 ms (timer period).
# While the stick is held, the value changes every STEP_EVERY samples.
# 3 -> one step per 300 ms (easy to control), 1 -> one step per 100 ms (fast)
STEP_EVERY = 3

# The joystick button cycles through these modes: (which target, step size).
# The X axis then changes that target by that step.
MODES = (
    ("temp", 1),
    ("temp", 0.1),
    ("hum", 1),
    ("hum", 0.1),
)

# How often we read the BME280 sensor
SENSOR_PERIOD_MS = 1000

# Relay that switches the heat source (1 = relay ON)
RELAY_PIN = 16

# Heater hysteresis (in degrees C):
# turn ON when the temperature is more than HEAT_ON_BELOW under the target,
# turn OFF when it is more than HEAT_OFF_ABOVE over the target
HEAT_ON_BELOW = 0.1
HEAT_OFF_ABOVE = 0.1

# Ventilator output (1 = fan running, 0 = stopped)
FAN_PIN = 18

# Cooling hysteresis (the heater logic mirrored, in degrees C):
# fan ON when the temperature is more than COOL_ON_ABOVE over the target,
# fan OFF when it is more than COOL_OFF_BELOW under the target
COOL_ON_ABOVE = 0.1
COOL_OFF_BELOW = 0.1

# NeoPixel strip: data pin and the number of LEDs on the strip.
# Set NUM_PIXELS to the real LED count, otherwise only the first ones light up.
# (A number that is too high does no harm, a number that is too low leaves LEDs dark.)
PIXEL_PIN = 2
NUM_PIXELS = 31

# The 4 colors (R, G, B). Level 40 of 255 is bright enough and not blinding.
LEVEL = 40
COLORS = (
    (LEVEL, LEVEL, LEVEL),   # white
    (0, 0, LEVEL),           # blue
    (LEVEL, 0, 0),           # red
    (0, LEVEL, 0),           # green
)


# Flag set by the timer, the main loop clears it
sample_due = False


def read_adc(adc, samples=8):
    # Average a few readings to reduce noise
    total = 0
    for _ in range(samples):
        total += adc.read_u16()

    average = round(total / samples)
    voltage = average * 3.3 / 65_535
    return average, voltage


def clamp(value, low, high):
    # Keep value between low and high
    return max(low, min(high, value))


def update_heater(heating, temperature, target_temp):
    # Two different thresholds (hysteresis), so the relay does not
    # click ON/OFF all the time when the temperature is close to the target.
    # Between the two thresholds we just keep the old state.
    if not heating and temperature < target_temp - HEAT_ON_BELOW:
        return True
    if heating and temperature > target_temp + HEAT_OFF_ABOVE:
        return False
    return heating


def update_cooler(cooling, temperature, target_temp):
    # Same idea as the heater, just reversed: too warm -> fan ON.
    # Between the two thresholds we keep the old state.
    if not cooling and temperature > target_temp + COOL_ON_ABOVE:
        return True
    if cooling and temperature < target_temp - COOL_OFF_BELOW:
        return False
    return cooling


def draw_measurement(oled, label, value, unit, x, y):
    # Label on the left, value 50 pixels to the right of it
    oled.text(label, x, y)
    oled.text(f"{value:.1f}{unit}", x + 50, y)


def draw_screen(oled, temperature, humidity, target_temp, target_hum, selected,
                heating, cooling):
    # Draw everything into the buffer and send it to the display once
    oled.fill(0)

    oled.text("CURRENT", 0, 0)
    # Status in the top right (heater and fan are never ON together)
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

    # Marker shows which target the joystick is changing right now,
    # and the step size is written at the end of that row
    field, step = MODES[selected]
    row_y = 40 if field == "temp" else 50
    oled.text(">", 0, row_y)
    oled.text("+1" if step >= 1 else "+.1", 104, row_y)

    oled.show()


def timer_handler(timer):
    # Keep the interrupt short, only set the flag
    global sample_due
    sample_due = True


def main():
    global sample_due

    target_temp = 20
    target_hum = 32
    selected = 0        # index in MODES (which target and which step size)
    hold_count = 0      # counts samples while the stick is held
    color_index = 0     # which color from COLORS is shown (0 = white)
    y_ready = True      # True when the stick is back in the middle

    # 400 kHz makes the OLED update about 4x faster than 100 kHz
    i2c = I2C(0, sda=Pin(4), scl=Pin(5), freq=400_000)
    oled = SH1106_I2C(i2c)
    sensor = BME280(i2c, addr=0x76)
    adc_x = ADC(27)
    adc_y = ADC(28)     # Y axis changes the NeoPixel color
    buttonjoy = Button(22)

    # All NeoPixels always get the same color, start with white
    pixel = neopixel.NeoPixel(Pin(PIXEL_PIN), NUM_PIXELS)
    pixel.fill(COLORS[color_index])
    pixel.write()

    # Relay starts OFF, so the heater is never on by accident at boot
    relay = Pin(RELAY_PIN, Pin.OUT)
    relay.value(0)
    heating = False

    # Ventilator also starts OFF
    fan = Pin(FAN_PIN, Pin.OUT)
    fan.value(0)
    cooling = False

    timer = Timer(-1)
    timer.init(
        period=100,
        mode=Timer.PERIODIC,
        callback=timer_handler
    )

    # First sensor reading, so we have values before the first redraw
    temperature = sensor.temperature()
    humidity = sensor.humidity()
    last_sensor_read = time.ticks_ms()

    needs_redraw = True

    try:
        while True:
            # --- Joystick button: next mode (temp 1, temp 0.1, hum 1, hum 0.1) ---
            if buttonjoy.was_pressed():
                selected = (selected + 1) % len(MODES)   # wraps back to 0
                needs_redraw = True

            # --- Joystick: only once per new ADC sample (every 100 ms) ---
            if sample_due:
                sample_due = False
                raw_x, voltage_x = read_adc(adc_x)

                direction = 0
                if raw_x >= JOY_HIGH:
                    direction = 1
                elif raw_x <= JOY_LOW:
                    direction = -1

                if direction == 0:
                    # Stick is in the middle, next push will react immediately
                    hold_count = 0
                else:
                    # Step on the first sample, then every STEP_EVERY samples
                    if hold_count == 0:
                        field, step = MODES[selected]
                        # round() removes float errors like 20.200000000000003
                        if field == "temp":
                            target_temp = round(clamp(target_temp + direction * step, TEMP_MIN, TEMP_MAX), 1)
                        else:
                            target_hum = round(clamp(target_hum + direction * step, HUM_MIN, HUM_MAX), 1)
                        needs_redraw = True
                    hold_count = (hold_count + 1) % STEP_EVERY

                # Y axis: push up/down = next/previous color.
                # One change per push, the stick must go back to the
                # middle before the next change (no fast color spinning).
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
                    # % wraps around: after the last color comes the first
                    color_index = (color_index + y_dir) % len(COLORS)
                    pixel.fill(COLORS[color_index])
                    pixel.write()

            # --- Sensor: read only about once per second ---
            if time.ticks_diff(time.ticks_ms(), last_sensor_read) >= SENSOR_PERIOD_MS:
                last_sensor_read = time.ticks_ms()
                # Temperature must be read first, humidity needs it
                temperature = sensor.temperature()
                humidity = sensor.humidity()
                needs_redraw = True

            # --- Heater: decide from the latest temperature and target ---
            # (runs every pass, so changing the target reacts immediately)
            new_heating = update_heater(heating, temperature, target_temp)
            if new_heating != heating:
                heating = new_heating
                relay.value(1 if heating else 0)
                needs_redraw = True

            # --- Cooling: same idea, but reversed ---
            new_cooling = update_cooler(cooling, temperature, target_temp)
            if new_cooling != cooling:
                cooling = new_cooling
                fan.value(1 if cooling else 0)
                needs_redraw = True

            # --- Display: redraw only when something changed ---
            if needs_redraw:
                needs_redraw = False
                draw_screen(oled, temperature, humidity,
                            target_temp, target_hum, selected, heating, cooling)

            time.sleep_ms(5)

    except KeyboardInterrupt:
        timer.deinit()
        oled.fill(0)
        oled.show()

    finally:
        # Whatever happens (Ctrl+C or a crash), never leave the heater or fan on
        relay.value(0)
        fan.value(0)
        pixel.fill((0, 0, 0))   # NeoPixels off
        pixel.write()


if __name__ == "__main__":
    main()