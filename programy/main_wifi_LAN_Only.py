"""
@file    main_wifi_LAN_Only.py
@brief   Smart Greenhouse Monitor - LAN version (control on the local network, optional ThingSpeak upload)

@details
The program measures the temperature and the humidity with a BME280 sensor and
shows the current and the target values on an SH1106 OLED display. It keeps the
values close to the targets with three outputs: a heater (relay), a ventilator
(fan) and a humidifier (relay). Every output uses hysteresis (two different
thresholds), so it does not switch on and off all the time.

@par Controls
 - Joystick button: selects what the X axis changes (temperature with step 1,
   temperature with step 0.1, humidity with step 1, humidity with step 0.1).
 - Joystick X axis: changes the selected target.
 - Joystick Y axis: changes the color of the NeoPixel strip (white, blue, red,
   green). All LEDs always have the same color.
 - Reset button: switches everything off and starts the program again.
 - Web page on the local network: shows the measured values and the state of
   the outputs, and sets both targets.

@par Regulation
 - Heater: ON below target - HEAT_ON_BELOW, OFF above target + HEAT_OFF_ABOVE.
 - Fan (cooling): ON above target + COOL_ON_ABOVE, OFF below
   target - COOL_OFF_BELOW.
 - Humidifier: ON when the humidity is below target - HUMID_ON_BELOW, OFF above
   target + HUMID_OFF_ABOVE.

@par Pins (Raspberry Pi Pico W)
 - GP4 / GP5: I2C0 (SDA / SCL), BME280 at 0x76 and SH1106 OLED at 0x3C
 - GP27 / GP28: joystick X axis / Y axis (ADC)
 - GP22: joystick button
 - GP17: reset button
 - GP16: heater relay (1 = on)
 - GP18: ventilator (1 = running)
 - GP19: humidifier relay (1 = on)
 - GP2: NeoPixel strip data

@par Network
The Pico joins the Wi-Fi given in config.py (SSID, PSWD) and serves a small web
page on port 80. The page is available only on the local network (LAN) and has
no password. If THINGSPEAK_KEY is set in config.py, the values are also sent to
ThingSpeak every UPLOAD_PERIOD_MS. The cloud is one way only: the Pico sends
data out and never reads anything back from it.

@par Files needed on the Pico
bme280.py, sh1106.py, gpio_devices.py, wifi_utils.py and config.py

@author  Vítek Borovka
@author  Václav Javůrek
@author  Jakub Kotačka
"""

from machine import I2C, Pin, ADC, Timer
from sh1106 import SH1106_I2C
import time
from bme280 import BME280
from gpio_devices import Button
import neopixel
import socket
import network
import wifi_utils
import urequests
import config   # has SSID, PSWD and THINGSPEAK_KEY (don't share this file!)


## @brief Lowest allowed target temperature in degrees C.
TEMP_MIN = 10
## @brief Highest allowed target temperature in degrees C.
TEMP_MAX = 35
## @brief Lowest allowed target humidity in percent.
HUM_MIN = 20
## @brief Highest allowed target humidity in percent.
HUM_MAX = 80

## @brief   Joystick limit: an ADC value above it counts as a push in the positive direction.
#  @details The 16-bit ADC gives 0 - 65535 and the middle is about 32768.
#           JOY_HIGH and JOY_LOW must be about the same distance from the
#           middle and not too close to 0 or 65535, because the stick usually
#           does not reach the very ends.
JOY_HIGH = 47000
## @brief Joystick limit: an ADC value below it counts as a push in the negative direction.
JOY_LOW = 18000

## @brief   Number of joystick reads between two steps while the stick is held.
#  @details The joystick is read every 100 ms (timer). 3 means one step per
#           300 ms (easy to control), 1 means one step per 100 ms (fast).
STEP_EVERY = 3

## @brief   Modes of the joystick button: (which target, step size).
#  @details The button goes through the modes in this order and the X axis
#           changes the selected target by the step of the current mode.
MODES = (
    ("temp", 1),
    ("temp", 0.1),
    ("hum", 1),
    ("hum", 0.1),
)

## @brief How often the BME280 sensor is read, in milliseconds.
SENSOR_PERIOD_MS = 1000

## @brief GPIO pin of the reset button (pressed = pin goes to 0).
RESET_PIN = 17

## @brief GPIO pin of the heater relay (1 = relay on).
RELAY_PIN = 16

## @brief   Heater switches ON when the temperature is more than this many
#           degrees under the target.
#  @details The gap between HEAT_ON_BELOW and HEAT_OFF_ABOVE stops the relay
#           from clicking all the time.
HEAT_ON_BELOW = 2
## @brief Heater switches OFF when the temperature is more than this many degrees over the target.
HEAT_OFF_ABOVE = 2

## @brief GPIO pin of the ventilator (1 = running, 0 = stopped).
FAN_PIN = 18

## @brief Fan switches ON when the temperature is more than this many degrees over the target.
COOL_ON_ABOVE = 2
## @brief Fan switches OFF when the temperature is more than this many degrees under the target.
COOL_OFF_BELOW = 2

## @brief GPIO pin of the humidifier relay (1 = humidifier running, 0 = stopped).
HUMID_PIN = 19

## @brief Humidifier switches ON when the humidity is more than this many percent under the target.
HUMID_ON_BELOW = 5
## @brief Humidifier switches OFF when the humidity is more than this many percent over the target.
HUMID_OFF_ABOVE = 5

## @brief GPIO pin of the NeoPixel strip data line.
PIXEL_PIN = 2
## @brief   Number of LEDs on the NeoPixel strip.
#  @details The Pico cannot find out the length of the strip by itself. If the
#           number is lower than the real count, the last LEDs stay dark. If it
#           is higher, nothing bad happens, the extra LEDs just do not exist.
NUM_PIXELS = 60

## @brief Brightness of the colors, 0 - 255 (40 is bright enough without hurting the eyes).
LEVEL = 40
## @brief The four colors of the strip as (R, G, B): white, blue, red, green.
COLORS = (
    (LEVEL, LEVEL, LEVEL),   # white
    (0, 0, LEVEL),           # blue
    (LEVEL, 0, 0),           # red
    (0, LEVEL, 0),           # green
)

## @brief TCP port of the web server (80 = http://IP/ without a port number).
WEB_PORT = 80

## @brief   Period of the Wi-Fi check and of the ThingSpeak upload, in milliseconds.
#  @details The free ThingSpeak account accepts a new value only every 15 s,
#           so 30 s is safe. If THINGSPEAK_KEY is missing in config.py, the
#           sending is skipped.
UPLOAD_PERIOD_MS = 30_000
## @brief Address of the ThingSpeak update API.
THINGSPEAK_URL = "https://api.thingspeak.com/update"


## @brief Flag set by the timer every 100 ms, cleared by the main loop.
sample_due = False


def read_adc(adc, samples=8):
    """
    @brief  Read an ADC channel several times and average the readings.
    @param  adc      ADC object (machine.ADC) to read.
    @param  samples  Number of readings to average (default 8).
    @return Tuple (average, voltage): the raw 16-bit average and the voltage in V.
    """
    # Take a few readings and average them, the ADC is a bit noisy
    total = 0
    for _ in range(samples):
        total += adc.read_u16()

    average = round(total / samples)
    voltage = average * 3.3 / 65_535
    return average, voltage


def clamp(value, low, high):
    """
    @brief  Limit a value to a range.
    @param  value  Value to limit.
    @param  low    Lowest allowed value.
    @param  high   Highest allowed value.
    @return The value, or low / high if it was outside the range.
    """
    return max(low, min(high, value))


def update_heater(heating, temperature, target_temp):
    """
    @brief  Decide if the heater should be on (hysteresis).
    @details Between the two thresholds the old state is kept, so the relay
             does not click on and off near the target.
    @param  heating      Current state of the heater (True = on).
    @param  temperature  Measured temperature in degrees C.
    @param  target_temp  Target temperature in degrees C.
    @return New state of the heater (True = on).
    """
    if not heating and temperature < target_temp - HEAT_ON_BELOW:
        return True
    if heating and temperature > target_temp + HEAT_OFF_ABOVE:
        return False
    return heating


def update_cooler(cooling, temperature, target_temp):
    """
    @brief  Decide if the fan should be on (the heater logic reversed).
    @details Too warm means fan on. Between the two thresholds the old state
             is kept.
    @param  cooling      Current state of the fan (True = running).
    @param  temperature  Measured temperature in degrees C.
    @param  target_temp  Target temperature in degrees C.
    @return New state of the fan (True = running).
    """
    if not cooling and temperature > target_temp + COOL_ON_ABOVE:
        return True
    if cooling and temperature < target_temp - COOL_OFF_BELOW:
        return False
    return cooling


def update_humidifier(humidifying, humidity, target_hum):
    """
    @brief  Decide if the humidifier should be on (the heater logic for humidity).
    @details Too dry means humidifier on (1). Between the two thresholds the
             old state is kept, so the relay does not click all the time.
    @param  humidifying  Current state of the humidifier (True = running).
    @param  humidity     Measured relative humidity in percent.
    @param  target_hum   Target relative humidity in percent.
    @return New state of the humidifier (True = running).
    """
    if not humidifying and humidity < target_hum - HUMID_ON_BELOW:
        return True
    if humidifying and humidity > target_hum + HUMID_OFF_ABOVE:
        return False
    return humidifying


def set_all_pixels(pixel, color):
    """
    @brief  Give every LED of the strip the same color and send it to the strip.
    @param  pixel  NeoPixel object. Its length (len(pixel)) is NUM_PIXELS.
    @param  color  Color as an (R, G, B) tuple.
    """
    # Go through the whole length of the strip, give every LED the same
    # color and send it to the strip once
    for i in range(len(pixel)):
        pixel[i] = color
    pixel.write()


def draw_measurement(oled, label, value, unit, x, y):
    """
    @brief  Draw one line "label value unit" into the display buffer.
    @param  oled   Display object (SH1106_I2C).
    @param  label  Text on the left, for example "Temp".
    @param  value  Number to show (one decimal place).
    @param  unit   Unit written after the number, for example "C".
    @param  x      X position of the label in pixels.
    @param  y      Y position of the line in pixels.
    """
    # The value is written 50 pixels further to the right than the label
    oled.text(label, x, y)
    oled.text(f"{value:.1f}{unit}", x + 50, y)


def draw_screen(oled, temperature, humidity, target_temp, target_hum, selected,
                heating, cooling, humidifying):
    """
    @brief  Draw the whole screen and send it to the display once.
    @details Shows the current values, the targets, a marker (>) with the step
             size at the selected target and HEAT / COOL in the top right
             corner. HUM is shown next to the humidity while the humidifier
             runs.
    @param  oled         Display object (SH1106_I2C).
    @param  temperature  Measured temperature in degrees C.
    @param  humidity     Measured humidity in percent.
    @param  target_temp  Target temperature in degrees C.
    @param  target_hum   Target humidity in percent.
    @param  selected     Index of the current mode in MODES.
    @param  heating      True while the heater is on.
    @param  cooling      True while the fan is on.
    @param  humidifying  True while the humidifier is on.
    """
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
    # HUM at the end of the humidity row while the humidifier is running
    if humidifying:
        oled.text("HUM", 104, 20)
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


def show_message(oled, line1, line2=""):
    """
    @brief  Show a short message on the display (startup messages).
    @param  oled   Display object (SH1106_I2C).
    @param  line1  First line of text.
    @param  line2  Second line of text (optional).
    """
    oled.fill(0)
    oled.text(line1, 0, 0)
    oled.text(line2, 0, 16)
    oled.show()


def build_page(temperature, humidity, target_temp, target_hum, heating, cooling,
               humidifying):
    """
    @brief  Build the HTML page of the web server.
    @details The page shows the measured values, the state of the outputs and a
             form that sets the two targets (GET request to /set).
    @param  temperature  Measured temperature in degrees C.
    @param  humidity     Measured humidity in percent.
    @param  target_temp  Target temperature in degrees C.
    @param  target_hum   Target humidity in percent.
    @param  heating      True while the heater is on.
    @param  cooling      True while the fan is on.
    @param  humidifying  True while the humidifier is on.
    @return The page as a string.
    """
    # The whole web page is one string. I only use inline styles, because
    # curly braces would clash with the f-string.
    if heating:
        status = "Heater ON"
    elif cooling:
        status = "Fan ON (cooling)"
    else:
        status = "Heater and fan OFF"
    humid_status = "Humidifier ON" if humidifying else "Humidifier OFF"

    return (
        "<!DOCTYPE html><html><head>"
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>Pico climate</title></head>"
        '<body style="font-family:sans-serif;max-width:360px;margin:20px auto;padding:0 12px">'
        "<h2>Pico climate control</h2>"
        f"<p>Now: <b>{temperature:.1f} C</b>, <b>{humidity:.1f} %</b><br>"
        f"Status: <b>{status}</b>, <b>{humid_status}</b></p>"
        '<form action="/set" method="get">'
        f'<p>Target temperature (C, {TEMP_MIN}-{TEMP_MAX}):<br>'
        f'<input type="number" name="temp" min="{TEMP_MIN}" max="{TEMP_MAX}" step="0.1" '
        f'value="{target_temp:.1f}" style="font-size:1.2em;width:100%"></p>'
        f'<p>Target humidity (%, {HUM_MIN}-{HUM_MAX}):<br>'
        f'<input type="number" name="hum" min="{HUM_MIN}" max="{HUM_MAX}" step="0.1" '
        f'value="{target_hum:.1f}" style="font-size:1.2em;width:100%"></p>'
        '<button type="submit" style="font-size:1.2em;padding:8px 16px">Set</button>'
        "</form></body></html>"
    )


def parse_query(path):
    """
    @brief  Split the query part of a request path into a dictionary.
    @param  path  Request path, for example "/set?temp=22.5&hum=40".
    @return Dictionary of strings, for example {"temp": "22.5", "hum": "40"}.
    """
    params = {}
    if "?" in path:
        query = path.split("?", 1)[1]
        for pair in query.split("&"):
            if "=" in pair:
                key, value = pair.split("=", 1)
                params[key] = value
    return params


def to_target(text, low, high):
    """
    @brief  Convert text from the web form to a target value.
    @param  text  Text from the form, for example "22.5".
    @param  low   Lowest allowed value.
    @param  high  Highest allowed value.
    @return Number inside the limits, rounded to 0.1.
    @throws ValueError if the text is not a number (also for NaN).
    """
    value = float(text)
    if value != value:      # only NaN is not equal to itself (trick to catch it)
        raise ValueError("not a number")
    return round(clamp(value, low, high), 1)


def handle_client(server, temperature, humidity, target_temp, target_hum,
                  heating, cooling, humidifying):
    """
    @brief  Answer one waiting browser request, if there is any.
    @details The socket is non-blocking. If nobody is connected, accept()
             raises OSError and the function returns right away, so the main
             loop is never blocked. Paths: "/" (page), "/set?temp=..&hum=.."
             (new targets, then a redirect back to "/"), anything else 404.
             If one of the two values is bad, none of them is changed.
    @param  server       Listening socket from start_server().
    @param  temperature  Measured temperature in degrees C.
    @param  humidity     Measured humidity in percent.
    @param  target_temp  Current target temperature.
    @param  target_hum   Current target humidity.
    @param  heating      True while the heater is on.
    @param  cooling      True while the fan is on.
    @param  humidifying  True while the humidifier is on.
    @return Tuple (target_temp, target_hum, changed): the targets (maybe new)
            and True if they were changed.
    """
    try:
        conn, addr = server.accept()
    except OSError:
        return target_temp, target_hum, False

    changed = False
    try:
        conn.settimeout(1)   # don't wait forever for a slow client
        request = conn.recv(1024)
        if not request:
            return target_temp, target_hum, False

        # The first line looks like: "GET /set?temp=22.5&hum=40 HTTP/1.1"
        first_line = request.split(b"\r\n")[0].decode()
        parts = first_line.split(" ")
        path = parts[1] if len(parts) > 1 else "/"

        if path.startswith("/set"):
            params = parse_query(path)
            try:
                # Convert both first, so one bad value changes nothing
                new_temp = to_target(params["temp"], TEMP_MIN, TEMP_MAX)
                new_hum = to_target(params["hum"], HUM_MIN, HUM_MAX)
                target_temp = new_temp
                target_hum = new_hum
                changed = True
            except (KeyError, ValueError):
                print("Web: bad values ignored")
            # Send the browser back to "/" so a refresh doesn't submit it again
            conn.sendall(b"HTTP/1.1 303 See Other\r\nLocation: /\r\n"
                         b"Connection: close\r\n\r\n")
        elif path == "/":
            page = build_page(temperature, humidity, target_temp, target_hum,
                              heating, cooling, humidifying)
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n"
                         b"Connection: close\r\n\r\n")
            conn.sendall(page.encode())
        else:
            # For example /favicon.ico, browsers ask for it by themselves
            conn.sendall(b"HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\n")

    except Exception as error:
        print("Web error:", error)
    finally:
        conn.close()   # always close it, otherwise we run out of sockets

    return target_temp, target_hum, changed


def start_server(port):
    """
    @brief  Create the web server socket (does not block the main loop).
    @param  port  TCP port to listen on.
    @return The listening, non-blocking socket.
    """
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", port))
    server.listen(2)
    server.setblocking(False)
    return server


def send_to_thingspeak(key, temperature, humidity, heating, cooling, humidifying):
    """
    @brief  Send the values to ThingSpeak (one way, nothing is read back).
    @details Fields of the channel: 1 = temperature, 2 = humidity, 3 = heater,
             4 = fan, 5 = humidifier. The request blocks for a moment (the internet
             is slower than the room Wi-Fi), so it is called only every
             UPLOAD_PERIOD_MS. The URL is never printed, because the API key is
             in it.
    @param  key          Write API key of the channel.
    @param  temperature  Measured temperature in degrees C.
    @param  humidity     Measured humidity in percent.
    @param  heating      True while the heater is on.
    @param  cooling      True while the fan is on.
    @param  humidifying  True while the humidifier is on.
    @return True if ThingSpeak accepted the values, False otherwise.
    """
    url = (f"{THINGSPEAK_URL}?api_key={key}"
           f"&field1={temperature:.1f}&field2={humidity:.1f}"
           f"&field3={1 if heating else 0}&field4={1 if cooling else 0}"
           f"&field5={1 if humidifying else 0}")
    try:
        response = urequests.get(url)
        entry = response.text.strip()   # number of the new entry, "0" = rejected
        response.close()                # always close it, we have few sockets
        if entry == "0":
            print("ThingSpeak: rejected (too fast or wrong key?)")
            return False
        print("ThingSpeak: sent, entry", entry)
        return True
    except Exception as error:
        print("ThingSpeak error:", error)
        return False


def timer_handler(timer):
    """
    @brief  Timer interrupt handler (every 100 ms).
    @details Keeps the interrupt short: it only sets the flag sample_due. The
             main loop does the real work.
    @param  timer  Timer object that called the handler.
    """
    global sample_due
    sample_due = True


def main():
    """
    @brief  Run the program: set up the hardware and run the main loop.
    @details The loop checks the buttons, reads the joystick (when the timer
             flag is set), reads the sensor once per second, decides about the
             heater, fan and humidifier, and redraws the display only when
             something changed.
             The reset button leaves the loop and main() returns True, so the
             program starts again from the top. Ctrl+C or a crash ends it. In
             every case the heater, fan, humidifier and the NeoPixel strip are
             switched off at the end.
    @return True if the reset button was pressed (start again), otherwise False.
    """
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

    # The humidifier also starts OFF
    humid = Pin(HUMID_PIN, Pin.OUT)
    humid.value(0)
    humidifying = False

    # Connect to the class Wi-Fi (can take a few seconds).
    # Heater, fan and humidifier are already OFF here, so nothing runs while I wait
    wifi = network.WLAN(network.STA_IF)
    server = None
    # The key is optional, without it the cloud part does nothing
    cloud_key = getattr(config, "THINGSPEAK_KEY", None)
    if cloud_key:
        print(f"Cloud: sending to ThingSpeak every {UPLOAD_PERIOD_MS // 1000} s")
    else:
        print("Cloud: THINGSPEAK_KEY not found in config.py, nothing will be sent")
    show_message(oled, "Connecting to", "Wi-Fi...")
    if wifi_utils.connect(wifi, config.SSID, config.PSWD):
        ip = wifi.ifconfig()[0]
        print(f"Open http://{ip}/ in your browser")
        server = start_server(WEB_PORT)
        show_message(oled, "Open in browser:", ip)
        time.sleep(3)   # gives me time to read the IP address
    else:
        # No Wi-Fi is not a big problem, the joystick still works without it
        # and I try to connect again later
        show_message(oled, "No Wi-Fi", "will retry later")
        time.sleep(2)

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
    # Pretend the last upload was long ago, so the first one happens right away
    last_upload = time.ticks_add(time.ticks_ms(), -UPLOAD_PERIOD_MS)

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

            # Web page: check if a browser sent a request.
            # This is before the heater and cooling, so a new target counts right away
            if server is not None:
                target_temp, target_hum, changed = handle_client(
                    server, temperature, humidity, target_temp, target_hum,
                    heating, cooling, humidifying)
                if changed:
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

            # Humidifier: same idea as the heater, but with the humidity.
            # Too dry = humidifier ON (1)
            new_humidifying = update_humidifier(humidifying, humidity, target_hum)
            if new_humidifying != humidifying:
                humidifying = new_humidifying
                humid.value(1 if humidifying else 0)
                needs_redraw = True

            # Display: only redraw when something changed
            if needs_redraw:
                needs_redraw = False
                draw_screen(oled, temperature, humidity,
                            target_temp, target_hum, selected,
                            heating, cooling, humidifying)

            # Every UPLOAD_PERIOD_MS: look after the Wi-Fi and send to the cloud.
            # It is after the display, so the screen is already up to date
            # when the sending blocks for a moment
            if time.ticks_diff(time.ticks_ms(), last_upload) >= UPLOAD_PERIOD_MS:
                last_upload = time.ticks_ms()
                if wifi.isconnected():
                    if server is None:
                        # Wi-Fi connected late, start the web page now
                        try:
                            server = start_server(WEB_PORT)
                        except OSError as error:
                            print("Web server error:", error)
                    if cloud_key:
                        send_to_thingspeak(cloud_key, temperature, humidity,
                                           heating, cooling, humidifying)
                else:
                    # Wi-Fi is gone, ask for a new connection and try again next time
                    print("Wi-Fi lost, reconnecting...")
                    wifi.connect(config.SSID, config.PSWD)

            time.sleep_ms(5)

    except KeyboardInterrupt:
        oled.fill(0)
        oled.show()

    finally:
        # No matter what happens (Ctrl+C, a crash or the reset button), never leave
        # the heater, fan or humidifier on. The timer has to be stopped too,
        # otherwise every restart would add one more timer
        timer.deinit()
        relay.value(0)
        fan.value(0)
        humid.value(0)
        set_all_pixels(pixel, (0, 0, 0))   # NeoPixels off
        if server is not None:
            server.close()
        if not restart:
            # On a restart I keep the Wi-Fi connected, so it starts faster
            wifi_utils.disconnect(wifi)

    # True = the reset button was pressed, so the program starts again
    return restart


if __name__ == "__main__":
    # main() gives back True when the reset button was pressed, then it just
    # runs again from the beginning. Ctrl+C or a crash ends the program
    while main():
        pass