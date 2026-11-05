from machine import I2C, Pin
from sh1106 import SH1106_I2C
import time
from bme280 import BME280


def draw_bar(oled, x, y, width, value, maximum):
    if width < 3:
        raise ValueError("Bar width must be at least 3 pixels")
    if maximum <= 0:
        raise ValueError("Maximum must be greater than zero")

    value = max(0, min(value, maximum))
    inner_width = width - 2
    filled_width = int(inner_width * value / maximum)
    oled.rect(x, y, width, 2, 1)
    oled.fill_rect(x + 1, y + 1, inner_width, 6, 0)
    if filled_width > 0:
        oled.fill_rect(x + 1, y + 1, filled_width, 2, 1)


def draw_measurement(oled, label, value, unit, y, maximum):
    oled.text(label, 0, y)
    oled.text(f"{value:.1f}{unit}", 50, y)
    draw_bar(oled, 0, y + 9, 120, value, maximum)


def main():
    i2c = I2C(0, sda=Pin(4), scl=Pin(5), freq=100_000)
    
    oled = SH1106_I2C(i2c)
    
    sensor = BME280(i2c, addr=0x76)

    #temperature = 23.5
    #humidity = 48.0
    #pressure = 1012.6

    

    try:
        while True:
            temperature, humidity, pressure, altitude = sensor.read_values()
            
            oled.fill(0)
            draw_measurement(oled, "Temp", temperature, "C", 0, 50)
            draw_measurement(oled, "Humi", humidity, "%", 15, 100)
            oled.show()
    
            time.sleep(2)

    except KeyboardInterrupt:
        oled.fill(0)
        oled.show()


if __name__ == "__main__":
    main()