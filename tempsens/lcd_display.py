"""
LCD display module for Pimoroni Enviro+ board.
Displays real-time sensor readings on ST7735 160x80 LCD screen with interactive modes.

Modes:
- Dashboard: All sensor readings at once (temperature, humidity, pressure, light)
- Graph: Rolling graph of selected sensor (cycles: temp -> humidity -> pressure -> light)

Switch modes by covering the proximity sensor (LTR559).
"""

import time
import socket
from typing import Optional, List, Deque
from collections import deque
from PIL import Image, ImageDraw, ImageFont

from . import io_funcs


class EnviroLCDDisplay:
    """LCD display manager for Pimoroni Enviro+ board with interactive modes."""

    def __init__(self, rotation=90, graph_history_length=80):
        """
        Initialize the ST7735 LCD display.

        Args:
            rotation: Display rotation in degrees (0, 90, 180, 270)
            graph_history_length: Number of data points to keep for graph mode
        """
        try:
            from ST7735 import ST7735
            from ltr559 import LTR559
        except ImportError:
            raise ImportError("ST7735 and ltr559 libraries not available - install with 'uv sync --extra enviroplus'")

        # Initialize display
        self.display = ST7735(
            port=0,
            cs=1,
            dc=9,
            backlight=12,
            rotation=rotation,
            spi_speed_hz=10000000
        )

        self.display.begin()

        # Initialize proximity sensor for mode switching
        self.ltr559 = LTR559()

        # Display dimensions (160x80 after rotation)
        self.width = self.display.width
        self.height = self.display.height

        # Load fonts
        try:
            self.font_large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20)
            self.font_medium = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
            self.font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 10)
        except Exception:
            # Fallback to default font if TrueType fonts not available
            self.font_large = ImageFont.load_default()
            self.font_medium = ImageFont.load_default()
            self.font_small = ImageFont.load_default()

        # Colors
        self.bg_color = (0, 0, 0)  # Black background
        self.text_color = (255, 255, 255)  # White text
        self.highlight_color = (0, 255, 0)  # Green for readings
        self.graph_color = (0, 200, 255)  # Cyan for graphs

        # Display modes
        self.modes = ["dashboard", "temp_graph", "humidity_graph", "pressure_graph", "light_graph"]
        self.current_mode_index = 0
        self.mode_names = {
            "dashboard": "Dashboard",
            "temp_graph": "Temperature",
            "humidity_graph": "Humidity",
            "pressure_graph": "Pressure",
            "light_graph": "Light"
        }

        # Graph history buffers
        self.graph_history_length = graph_history_length
        self.temp_history: Deque[Optional[float]] = deque(maxlen=graph_history_length)
        self.humidity_history: Deque[Optional[float]] = deque(maxlen=graph_history_length)
        self.pressure_history: Deque[Optional[float]] = deque(maxlen=graph_history_length)
        self.light_history: Deque[Optional[float]] = deque(maxlen=graph_history_length)

        # Proximity sensor state for mode switching
        self.last_proximity = 0
        self.proximity_trigger_threshold = 1500  # Trigger when hand covers sensor

    def _get_device_ip(self) -> str:
        """Get local IP address."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "no network"

    def check_mode_switch(self):
        """
        Check proximity sensor and switch mode if triggered.
        Returns True if mode was switched.
        """
        try:
            proximity = self.ltr559.get_proximity()

            # Detect rising edge (hand covering sensor)
            if proximity > self.proximity_trigger_threshold and self.last_proximity <= self.proximity_trigger_threshold:
                # Switch to next mode
                self.current_mode_index = (self.current_mode_index + 1) % len(self.modes)
                current_mode = self.modes[self.current_mode_index]
                print(f"Switched to mode: {self.mode_names[current_mode]}")
                self.last_proximity = proximity
                return True

            self.last_proximity = proximity
            return False
        except Exception:
            return False

    def add_reading(self, temperature: Optional[float], humidity: Optional[float],
                   pressure: Optional[float] = None, light: Optional[float] = None):
        """
        Add sensor readings to history buffers for graphing.

        Args:
            temperature: Temperature in Celsius
            humidity: Humidity percentage
            pressure: Atmospheric pressure in hPa
            light: Light level in lux
        """
        self.temp_history.append(temperature)
        self.humidity_history.append(humidity)
        self.pressure_history.append(pressure)
        self.light_history.append(light)

    def draw_dashboard(self, temperature: Optional[float], humidity: Optional[float],
                      pressure: Optional[float] = None, light: Optional[float] = None):
        """
        Draw dashboard view with all sensor readings.

        Args:
            temperature: Temperature in Celsius
            humidity: Humidity percentage
            pressure: Atmospheric pressure in hPa
            light: Light level in lux
        """
        # Create blank image
        img = Image.new('RGB', (self.width, self.height), color=self.bg_color)
        draw = ImageDraw.Draw(img)

        # Get device info
        config = io_funcs.fetch_config()
        device_name = config["DEFAULT"].get("device_name", socket.gethostname())
        ip_address = self._get_device_ip()

        # Layout parameters
        y_offset = 2
        line_height = 18

        # Draw device name (top line, small font)
        draw.text((2, y_offset), device_name[:20], font=self.font_small, fill=self.text_color)
        y_offset += 12

        # Draw temperature
        if temperature is not None:
            temp_text = f"{temperature:.1f}°C"
            draw.text((2, y_offset), temp_text, font=self.font_large, fill=self.highlight_color)
        else:
            draw.text((2, y_offset), "-- °C", font=self.font_large, fill=(255, 0, 0))
        y_offset += line_height

        # Draw humidity
        if humidity is not None:
            hum_text = f"{humidity:.1f}%"
            draw.text((2, y_offset), hum_text, font=self.font_large, fill=self.highlight_color)
        else:
            draw.text((2, y_offset), "-- %", font=self.font_large, fill=(255, 0, 0))
        y_offset += line_height

        # Draw pressure (if available)
        if pressure is not None:
            pressure_text = f"{pressure:.0f}hPa"
            draw.text((2, y_offset), pressure_text, font=self.font_small, fill=self.text_color)
        y_offset += 12

        # Draw light level (if available)
        if light is not None:
            light_text = f"{light:.0f}lux"
            draw.text((90, y_offset), light_text, font=self.font_small, fill=self.text_color)

        # Draw IP address (bottom line)
        draw.text((2, self.height - 12), ip_address, font=self.font_small, fill=self.text_color)

        # Display the image
        self.display.display(img)

    def draw_graph(self, data_history: Deque[Optional[float]], title: str, unit: str, color: tuple):
        """
        Draw a rolling graph of sensor data.

        Args:
            data_history: Deque of historical data points
            title: Graph title (e.g., "Temperature")
            unit: Unit string (e.g., "°C")
            color: RGB tuple for graph line color
        """
        # Create blank image
        img = Image.new('RGB', (self.width, self.height), color=self.bg_color)
        draw = ImageDraw.Draw(img)

        # Draw title
        draw.text((2, 2), title, font=self.font_small, fill=self.text_color)

        # Filter out None values for scaling
        valid_data = [v for v in data_history if v is not None]

        if len(valid_data) < 2:
            # Not enough data to draw graph
            draw.text((self.width // 2 - 30, self.height // 2), "No data", font=self.font_small, fill=self.text_color)
            self.display.display(img)
            return

        # Calculate scaling
        min_val = min(valid_data)
        max_val = max(valid_data)
        value_range = max_val - min_val

        # Add 10% padding to range
        if value_range > 0:
            padding = value_range * 0.1
            min_val -= padding
            max_val += padding
            value_range = max_val - min_val
        else:
            # All values are the same
            min_val -= 1
            max_val += 1
            value_range = 2

        # Graph area (leave space for title and current value)
        graph_top = 15
        graph_bottom = self.height - 15
        graph_height = graph_bottom - graph_top
        graph_left = 5
        graph_right = self.width - 5
        graph_width = graph_right - graph_left

        # Draw current value
        if data_history[-1] is not None:
            current_text = f"{data_history[-1]:.1f}{unit}"
            draw.text((self.width - 60, self.height - 12), current_text, font=self.font_small, fill=self.highlight_color)

        # Draw graph lines
        data_list = list(data_history)
        for i in range(1, len(data_list)):
            if data_list[i - 1] is not None and data_list[i] is not None:
                # Calculate x positions (spread across graph width)
                x1 = graph_left + int((i - 1) * graph_width / (self.graph_history_length - 1))
                x2 = graph_left + int(i * graph_width / (self.graph_history_length - 1))

                # Calculate y positions (inverted because screen y=0 is top)
                y1 = graph_bottom - int((data_list[i - 1] - min_val) / value_range * graph_height)
                y2 = graph_bottom - int((data_list[i] - min_val) / value_range * graph_height)

                # Draw line segment
                draw.line([(x1, y1), (x2, y2)], fill=color, width=2)

        # Draw min/max labels
        draw.text((2, graph_top), f"{max_val:.0f}", font=self.font_small, fill=self.text_color)
        draw.text((2, graph_bottom - 10), f"{min_val:.0f}", font=self.font_small, fill=self.text_color)

        # Display the image
        self.display.display(img)

    def update_display(self, temperature: Optional[float], humidity: Optional[float],
                      pressure: Optional[float] = None, light: Optional[float] = None):
        """
        Update display based on current mode.

        Args:
            temperature: Temperature in Celsius
            humidity: Humidity percentage
            pressure: Atmospheric pressure in hPa
            light: Light level in lux
        """
        # Add readings to history
        self.add_reading(temperature, humidity, pressure, light)

        # Get current mode
        mode = self.modes[self.current_mode_index]

        # Draw based on mode
        if mode == "dashboard":
            self.draw_dashboard(temperature, humidity, pressure, light)
        elif mode == "temp_graph":
            self.draw_graph(self.temp_history, "Temperature", "°C", self.graph_color)
        elif mode == "humidity_graph":
            self.draw_graph(self.humidity_history, "Humidity", "%", self.graph_color)
        elif mode == "pressure_graph":
            self.draw_graph(self.pressure_history, "Pressure", "hPa", (255, 150, 0))
        elif mode == "light_graph":
            self.draw_graph(self.light_history, "Light", "lux", (255, 255, 0))

    def clear(self):
        """Clear the display."""
        img = Image.new('RGB', (self.width, self.height), color=self.bg_color)
        self.display.display(img)

    def show_startup_message(self, message: str):
        """Show a startup/status message."""
        img = Image.new('RGB', (self.width, self.height), color=self.bg_color)
        draw = ImageDraw.Draw(img)

        # Center the message
        draw.text((5, self.height // 2 - 10), message, font=self.font_medium, fill=self.text_color)
        self.display.display(img)


def run_lcd_display_loop(update_interval_s: float = 1.0):
    """
    Main loop for updating LCD display with sensor readings and interactive mode switching.

    Args:
        update_interval_s: Seconds between display updates (default: 1.0s for responsive mode switching)
    """
    print("Initializing Enviro+ LCD display...")

    try:
        display = EnviroLCDDisplay(rotation=90, graph_history_length=80)
        display.show_startup_message("Starting up...")
        time.sleep(2)
    except Exception as e:
        print(f"Failed to initialize LCD display: {e}")
        return

    print("LCD display initialized successfully")
    print("Cover the proximity sensor to switch between modes:")
    print("  1. Dashboard (all sensors)")
    print("  2. Temperature graph")
    print("  3. Humidity graph")
    print("  4. Pressure graph")
    print("  5. Light graph")

    # Get sensor instance
    sensor = io_funcs._get_sensor()
    print(f"Using sensor: {sensor.name}")

    # Check if sensor supports extended readings
    has_extended = hasattr(sensor, 'read_extended')

    # Track consecutive errors
    error_count = 0
    max_errors = 5

    while True:
        try:
            # Check for mode switch
            display.check_mode_switch()

            # Read sensor data
            if has_extended:
                data = sensor.read_extended()
                temperature = data.get("temperature")
                humidity = data.get("humidity")
                pressure = data.get("pressure")
                light = data.get("light")
            else:
                temperature, humidity = sensor.read()
                pressure, light = None, None

            # Update display with current mode
            display.update_display(temperature, humidity, pressure, light)

            # Reset error counter on success
            error_count = 0

        except RuntimeError as e:
            # Transient sensor error (checksum, timeout) - retry silently
            error_count += 1
            if error_count >= max_errors:
                print(f"Error updating display (after {error_count} attempts): {e}")
                # Show error on display
                display.clear()
                img = Image.new('RGB', (display.width, display.height), color=(0, 0, 0))
                draw = ImageDraw.Draw(img)
                draw.text((5, 30), "Sensor Error", font=display.font_medium, fill=(255, 0, 0))
                display.display.display(img)
            time.sleep(0.5)  # Shorter retry interval
            continue

        except Exception as e:
            print(f"Unexpected error in display loop: {e}")
            import traceback
            traceback.print_exc()
            time.sleep(update_interval_s)
            continue

        time.sleep(update_interval_s)


if __name__ == "__main__":
    # Run standalone LCD display loop
    # Update every 1 second for responsive mode switching
    run_lcd_display_loop(update_interval_s=1.0)
