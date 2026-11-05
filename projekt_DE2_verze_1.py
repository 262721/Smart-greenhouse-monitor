from machine import I2C, Pin , ADC , Timer
from sh1106 import SH1106_I2C
import time
from bme280 import BME280
from gpio_devices import Button, PWMLed


""" def draw_bar(oled, x, y, width, value, maximum):
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
        oled.fill_rect(x + 1, y + 1, filled_width, 2, 1) """


def read_adc(adc, samples=8):
    total = 0
    for _ in range(samples):
        total += adc.read_u16()

    average = round(total / samples)
    voltage = average * 3.3 / 65_535
    return average, voltage



def draw_measurement(oled, label, value, unit, x,y):
    oled.text(label, x, y)
    oled.text(f"{value:.1f}{unit}", 50, y)
    

def timer_handler(timer):
    global sample_due

    sample_due = True


sample_due = False
target_temp = 20
target_hum = 32
switchbut = 0
raw_x = 10

def main():
    global sample_due
    global target_temp
    global target_hum
    global switchbut 
    global raw_x

    i2c = I2C(0, sda=Pin(4), scl=Pin(5), freq=100_000)
    oled = SH1106_I2C(i2c)
    sensor = BME280(i2c, addr=0x76)
    adc_x = ADC(27)
    adc_y = ADC(28)
    buttonjoy = Button(22)


    timer = Timer(-1)
    timer.init(
        period=100,
        mode=Timer.PERIODIC,
        callback=timer_handler
    )

    try:
        while True:
            if buttonjoy.was_pressed():
                if switchbut == 0:
                    switchbut = 1
                else:
                    switchbut = 0
                print(switchbut)



            if sample_due:
                sample_due = False
                raw_x, voltage_x = read_adc(adc_x)
                raw_y, voltage_y = read_adc(adc_y)
                """ print(
                    f"\rX: {raw_x:5d} ({voltage_x:.2f} V)  "
                    f"Y: {raw_y:5d} ({voltage_y:.2f} V)  "
                ) """
                

            if switchbut == 0:  
                if raw_x >= 55000:
                    target_temp+=1
                if raw_x <= 450 :
                    target_temp-=1
            else:
                if raw_x >= 55000:
                    target_hum+=1
                if raw_x <= 450 :
                    target_hum-=1

                
                # Read sensor and update OLED inside the timer block 
                # or at a controlled non-blocking interval
                temperature, humidity, pressure, altitude = sensor.read_values()
                
                oled.fill(0)
                oled.text("CURRENT", 0, 0)
                oled.line(0, 8, 127, 8, 1)
                draw_measurement(oled, "Temp", temperature, "C", 0, 10)
                draw_measurement(oled, "Humi", humidity, "%", 0, 20)
                oled.line(0, 28, 127, 28, 1)
                oled.text("TARGET", 0, 30)
                oled.line(0, 38, 127, 38, 1)
                draw_measurement(oled, "Temp", target_temp, "C", 0, 40)
                draw_measurement(oled, "Humi", target_hum, "%", 0, 50)
                oled.line(0, 58, 127, 58, 1)
                oled.show()

            time.sleep_ms(5)

    except KeyboardInterrupt:
        oled.fill(0)
        oled.show()


if __name__ == "__main__":
    main()