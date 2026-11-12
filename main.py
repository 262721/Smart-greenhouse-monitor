from machine import I2C, Pin, ADC, Timer
from sh1106 import SH1106_I2C
import time
from bme280 import BME280
from gpio_devices import Button


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

# How often we read the BME280 sensor
SENSOR_PERIOD_MS = 1000


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


def draw_measurement(oled, label, value, unit, x, y):
    # Label on the left, value 50 pixels to the right of it
    oled.text(label, x, y)
    oled.text(f"{value:.1f}{unit}", x + 50, y)


def draw_screen(oled, temperature, humidity, target_temp, target_hum, selected):
    # Draw everything into the buffer and send it to the display once
    oled.fill(0)

    oled.text("CURRENT", 0, 0)
    oled.line(0, 8, 127, 8, 1)
    draw_measurement(oled, "Temp", temperature, "C", 10, 10)
    draw_measurement(oled, "Humi", humidity, "%", 10, 20)
    oled.line(0, 28, 127, 28, 1)

    oled.text("TARGET", 0, 30)
    oled.line(0, 38, 127, 38, 1)
    draw_measurement(oled, "Temp", target_temp, "C", 10, 40)
    draw_measurement(oled, "Humi", target_hum, "%", 10, 50)
    oled.line(0, 58, 127, 58, 1)

    # Marker shows which target the joystick is changing right now
    if selected == 0:
        oled.text(">", 0, 40)
    else:
        oled.text(">", 0, 50)

    oled.show()


def timer_handler(timer):
    # Keep the interrupt short, only set the flag
    global sample_due
    sample_due = True


def main():
    global sample_due

    target_temp = 20
    target_hum = 32
    selected = 0        # 0 = editing target temperature, 1 = target humidity
    hold_count = 0      # counts samples while the stick is held

    # 400 kHz makes the OLED update about 4x faster than 100 kHz
    i2c = I2C(0, sda=Pin(4), scl=Pin(5), freq=400_000)
    oled = SH1106_I2C(i2c)
    sensor = BME280(i2c, addr=0x76)
    adc_x = ADC(27)
    adc_y = ADC(28)     # not used at the moment
    buttonjoy = Button(22)

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
            # --- Joystick button: switch between temp and humidity ---
            if buttonjoy.was_pressed():
                selected ^= 1   # flips 0 <-> 1
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
                        if selected == 0:
                            target_temp = clamp(target_temp + direction, TEMP_MIN, TEMP_MAX)
                        else:
                            target_hum = clamp(target_hum + direction, HUM_MIN, HUM_MAX)
                        needs_redraw = True
                    hold_count = (hold_count + 1) % STEP_EVERY

            # --- Sensor: read only about once per second ---
            if time.ticks_diff(time.ticks_ms(), last_sensor_read) >= SENSOR_PERIOD_MS:
                last_sensor_read = time.ticks_ms()
                # Temperature must be read first, humidity needs it
                temperature = sensor.temperature()
                humidity = sensor.humidity()
                needs_redraw = True

            # --- Display: redraw only when something changed ---
            if needs_redraw:
                needs_redraw = False
                draw_screen(oled, temperature, humidity,
                            target_temp, target_hum, selected)

            time.sleep_ms(5)

    except KeyboardInterrupt:
        timer.deinit()
        oled.fill(0)
        oled.show()


if __name__ == "__main__":
    main()
