"""
This module provides classes to manage common input/output components, 
such as buttons and LEDs, with support for PWM-based brightness control 
for LEDs. The classes allow for checking button states, toggling LEDs, 
blinking LEDs, and controlling brightness and fading effects using PWM.

Example
-------
.. code-block:: python

    from gpio_devices import Led, PWMLed

    led = Led(2)

    print("LED blinking...")
    led.blink(times=3)

    print("Toggling LED...")
    led.toggle()

    # Example of using the PWMLed class
    led = PWMLed(3)

    print("Fading in...")
    led.fade_in(duration=2)

Author
------
Tomas Fryza

Modification history
--------------------
- **2026-10-05** : Button implemented simple debouncing and other methods. RGBLed class added to control RGB LEDs. `pulse` method added to Led class. PWMLed methods substituted `duty` to `duty_u16` for Raspberry Pi Pico.
- **2025-10-15** : Button redefined so that it inherits from Pin.
- **2024-11-11** : Added Sphinx-style comments for documentation.
- **2024-10-26** : Added `demo` method to demonstrate usage of the classes.
- **2024-09-28** : File created, initial release.
"""

from machine import Pin
from machine import PWM
import time


class Button(Pin):
    """
    A class to manage a button connected to a GPIO pin with an 
    internal pull-up resistor.
    """

    def __init__(self, pin_number):
        """
        Initialize the button on a specific GPIO pin with a pull-up resistor.

        :param pin_number: GPIO pin number where the button is connected.
        """
        self.pin = Pin(pin_number, Pin.IN, Pin.PULL_UP)
        self._raw_value = self.pin.value()
        self._stable_value = self._raw_value
        self._last_change = time.ticks_ms()

    def is_pressed(self):
        """
        Check if the button is currently pressed using active-low logic.

        :return: `True` if the button is pressed; `False` otherwise.
        """
        return not self.pin.value()

    def is_released(self):
        """
        Check whether the button is currently released.

        :return: ``True`` if the button is released; ``False`` otherwise.
        """
        return self.pin.value() == 1
    
    def button_state(self):
        """
        Read the current button state.

        :return: ``'pressed'`` if the button is pressed; ``'released'`` otherwise.
        """
        if self.is_pressed():
            return "pressed"
        else:
            return "released"

    def was_pressed(self, debounce_ms=20):
        """
        Check whether a stable button press has occurred.

        A press is reported once after the input remains stable for the debounce
        interval.

        :param debounce_ms: Debounce interval in milliseconds. Defaults to 20.
        :return: ``True`` once when a stable press is detected; ``False`` otherwise.
        """
        raw_value = self.pin.value()
        if raw_value != self._raw_value:
            self._raw_value = raw_value
            self._last_change = time.ticks_ms()

        elapsed = time.ticks_diff(time.ticks_ms(), self._last_change)
        if elapsed >= debounce_ms and raw_value != self._stable_value:
            self._stable_value = raw_value
            return raw_value == 0

        return False


class Led(Pin):
    """
    A class to control an LED connected to a specified GPIO pin.
    """

    def __init__(self, pin_number):
        """
        Initialize the LED on a specific GPIO pin.

        :param pin_number: GPIO pin number where the LED is connected.
        """
        # Calls Parent's __init__ without needing `self`
        super().__init__(pin_number, Pin.OUT)

    def toggle(self):
        """
        Toggle the LED state between on and off.
        """
        self.value(not self.value())

    def pulse(self, duration=0.25):
        """
        Turn the LED on briefly, then turn it off.

        :param duration: Time in seconds to keep the LED on. Defaults to 0.25.
        """
        self.value(1)
        time.sleep(duration)
        self.value(0)

    def blink(self, duration=0.5, times=5):
        """
        Blink the LED a specified number of times.

        :param duration: Duration in seconds for each on/off cycle.
                         Default is 0.5 seconds.
        :param times: Number of times the LED should blink. Default is 5.
        """
        for _ in range(times):
            self.on()
            time.sleep(duration)
            self.off()
            time.sleep(duration)


class RGBLed:
    """
    Control a common-cathode RGB LED using three GPIO pins.
    """

    def __init__(self, red_pin, green_pin, blue_pin):
        """
        Initialize the RGB LED with separate red, green, and blue pins.

        :param red_pin: GPIO pin connected to the red channel.
        :param green_pin: GPIO pin connected to the green channel.
        :param blue_pin: GPIO pin connected to the blue channel.
        """
        self.red_pin = Pin(red_pin, Pin.OUT)
        self.green_pin = Pin(green_pin, Pin.OUT)
        self.blue_pin = Pin(blue_pin, Pin.OUT)
        self.off()

    def set_color(self, red, green, blue):
        """
        Set the state of each color channel.

        :param red: Red channel state: ``0`` for off or ``1`` for on.
        :param green: Green channel state: ``0`` for off or ``1`` for on.
        :param blue: Blue channel state: ``0`` for off or ``1`` for on.
        """
        self.red_pin.value(red)
        self.green_pin.value(green)
        self.blue_pin.value(blue)

    def off(self):
        """
        Turn all color channels off.
        """
        self.set_color(0, 0, 0)

    def red(self):
        """
        Display red.
        """
        self.set_color(1, 0, 0)

    def green(self):
        """
        Display green.
        """
        self.set_color(0, 1, 0)

    def blue(self):
        """
        Display blue.
        """
        self.set_color(0, 0, 1)

    def yellow(self):
        """
        Display yellow by turning on red and green.
        """
        self.set_color(1, 1, 0)

    def white(self):
        """
        Display white by turning on all three color channels.
        """
        self.set_color(1, 1, 1)

    def blink(self, color=(0, 1, 0), duration=0.5, times=2):
        """
        Blink the LED using the specified color.

        :param color: ``(red, green, blue)`` tuple with each channel set to
                      ``0`` (off) or ``1`` (on). Defaults to green.
        :param duration: Duration in seconds for each on and off phase.
                         Defaults to 0.5.
        :param times: Number of blink cycles. Defaults to 2.
        """
        for _ in range(times):
            self.set_color(*color)
            time.sleep(duration)
            self.off()
            time.sleep(duration)


class PWMLed(PWM):
    """
    A class to control an LED using PWM, allowing for brightness
    adjustment, fading, and on/off control.
    """

    def __init__(self, pin_number, frequency=1000):
        """
        Initialize PWM on the specified pin with a given frequency,
        starting with LED off.

        :param pin_number: GPIO pin number where the LED is connected.
        :param frequency: PWM frequency for LED control. Default is 1000 Hz.
        """
        pin = Pin(pin_number, Pin.OUT)
        super().__init__(pin)
        self.freq(frequency)
        self.duty_u16(0)
    
    def set_brightness(self, brightness):
        """
        Set the LED brightness using PWM.

        :param brightness: Brightness level as a percentage (0 to 100).
        """
        if not 0 <= brightness <= 100:
            raise ValueError("brightness must be between 0 and 100")

        duty_cycle = round(brightness * 65535 / 100)
        self.duty_u16(duty_cycle)

    def on(self, brightness=100):
        """
        Turn the LED on by setting it to a specified brightness level.

        :param brightness: Brightness level as a percentage (0 to 100).
                           Default is 100%.
        """
        self.set_brightness(brightness)

    def off(self):
        """
        Turn the LED off by setting the brightness to 0.
        """
        self.set_brightness(0)

    def fade_in(self, duration=1):
        """
        Gradually increase the brightness to create a fade-in effect.

        :param duration: Total duration of the fade-in effect, in seconds.
                         Default is 1 second.
        """
        step_duration = duration / 100
        for i in range(100):
            self.set_brightness(i)
            time.sleep(step_duration)

    def fade_out(self, duration=1):
        """
        Gradually decrease the brightness to create a fade-out effect.

        :param duration: Total duration of the fade-out effect, in seconds.
                         Default is 1 second.
        """
        step_duration = duration / 100
        for i in range(100, -1, -1):  # -1 to reach fully off
            self.set_brightness(i)
            time.sleep(step_duration)


def demo_button():
    """
    Demonstrates usage of the `Button` and `RGBLed` classes.
    """
    button = Button(17)
    rgb_led = RGBLed(12, 13, 14)
    colors = [(1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 1, 0), (1, 1, 1)]
    idx = 0

    print("Press the button to cycle through colors on the RGB LED; press Ctrl+C to stop.")

    try:
        while True:
            if button.was_pressed():
                print(f"Button pressed; color={colors[idx]}")
                rgb_led.blink(color=colors[idx], duration=0.1, times=3)
                idx = (idx + 1) % len(colors)
            time.sleep_ms(1)

    except KeyboardInterrupt:
        print("\nProgram stopped. Exiting...")
        rgb_led.off()


def demo_leds():
    """Demonstrates usage of the `Led` and `PWMLed` classes."""
    led = Led(2)

    print("LED blinking...")
    led.blink(times=3)

    print("Toggling LED...")
    led.toggle()
    time.sleep(1)

    print("Turning LED off...")
    led.off()
    time.sleep(1)


    # Example of using the PWMLed class
    led = PWMLed(3)

    print("\nFading in...")
    led.fade_in(duration=2)

    print("Fading out...")
    led.fade_out(duration=2)
    time.sleep(1)

    print("\nLED on at 10% brightness...")
    led.on(10)
    time.sleep(1)
    print("LED on at 40% brightness...")
    led.on(40)
    time.sleep(1)
    print("LED on at 100% brightness...")
    led.on(100)
    time.sleep(1)

    print("\nTurning LED off...")
    led.off()
    time.sleep(1)

    print("\nTesting class relationship...")
    print(f"issubclass(Led, Pin): {issubclass(Led, Pin)}")
    print(f"issubclass(PWMLed, Led): {issubclass(PWMLed, Led)}")
    print(f"issubclass(Led, PWMLed): {issubclass(Led, PWMLed)}")
    print(f"isinstance(led, PWM): {isinstance(led, PWM)}")


if __name__ == "__main__" :
    # demo_button()
    demo_leds()
