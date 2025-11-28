"""
LCD display module for Pimoroni Enviro+ board.
Displays real-time sensor readings on ST7735 160x80 LCD screen with interactive modes.

Modes:
- Temperature: Color gradient graph with large value display
- Humidity: Color gradient graph with large value display
- Pressure: Color gradient graph with large value display
- Light: Color gradient graph with large value display

Switch modes by covering the proximity sensor (LTR559).

Inspired by Pimoroni's all-in-one-enviro-mini.py and weather-and-light.py examples.
"""

import time
import colorsys
from typing import Optional, List, Dict
from PIL import Image, ImageDraw, ImageFont

from . import io_funcs


class EnviroLCDDisplay:
    """LCD display manager for Pimoroni Enviro+ board with interactive modes."""

    def __init__(self, rotation=90, history_length=160):
        """
        Initialize the ST7735 LCD display.

        Args:
            rotation: Display rotation in degrees (0, 90, 180, 270)
            history_length: Number of data points to keep for graph mode (matches display width)
        """
        try:
            from st7735 import ST7735
            from ltr559 import LTR559
        except ImportError as e:
            raise ImportError(f"ST7735 and ltr559 libraries not available - install with 'uv sync --extra enviroplus'. Error: {e}")

        # Initialize display
        self.display = ST7735(
            port=0,
            cs=1,
            dc="GPIO9",
            backlight="GPIO12",
            rotation=rotation,
            spi_speed_hz=10000000
        )

        self.display.begin()

        # Initialize proximity sensor for mode switching
        self.ltr559 = LTR559()

        # Display dimensions (160x80 after rotation)
        self.width = self.display.width
        self.height = self.display.height

        # Load fonts - try RobotoMedium first (Pimoroni's preferred font), then DejaVu, then default
        self.font = self._load_font(20)
        self.font_sm = self._load_font(12)
        self.font_lg = self._load_font(14)

        # Position for the top text bar (below which the graph is drawn)
        self.top_bar_height = 25

        # Display modes - cycle through each sensor
        self.variables = ["temperature", "pressure", "humidity", "light"]
        self.units = {
            "temperature": "°C",
            "pressure": "hPa",
            "humidity": "%",
            "light": "Lux"
        }
        self.current_mode = 0

        # Data history for each variable (for graphing)
        self.values: Dict[str, List[float]] = {}
        for v in self.variables:
            self.values[v] = [1.0] * self.width  # Initialize with 1s to avoid division by zero

        # Proximity sensor state for mode switching
        self.last_page_time = 0
        self.proximity_debounce = 0.3  # seconds (fast response)
        self.proximity_threshold = 1500

        # Custom color scheme - cyan/blue on black
        self.bg_color = (0, 0, 0)  # Black background
        self.text_color = (0, 200, 255)  # Cyan text
        self.accent_color = (0, 150, 200)  # Darker cyan for accents
        self.graph_line_color = (0, 255, 200)  # Bright cyan-green for graph line

    def _load_font(self, size: int) -> ImageFont.ImageFont:
        """Load the best available font at the specified size."""
        font_paths = [
            # Pimoroni's preferred font
            "/usr/share/fonts/truetype/roboto/unhinted/RobotoTTF/Roboto-Medium.ttf",
            # fonts.ttf package location
            None,  # Will try fonts.ttf import
            # DejaVu fallback
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]

        # Try to import from fonts.ttf package (Pimoroni's approach)
        try:
            from fonts.ttf import RobotoMedium as UserFont
            return ImageFont.truetype(UserFont, size)
        except ImportError:
            pass

        # Try each font path
        for font_path in font_paths:
            if font_path is None:
                continue
            try:
                return ImageFont.truetype(font_path, size)
            except Exception:
                continue

        # Fallback to default
        return ImageFont.load_default()

    def check_mode_switch(self) -> bool:
        """
        Check proximity sensor and switch mode if triggered.
        Returns True if mode was switched.
        """
        try:
            proximity = self.ltr559.get_proximity()

            # Detect proximity crossing threshold with debounce
            if proximity > self.proximity_threshold and time.time() - self.last_page_time > self.proximity_debounce:
                self.current_mode += 1
                self.current_mode %= len(self.variables)
                self.last_page_time = time.time()
                current_var = self.variables[self.current_mode]
                print(f"Switched to mode: {current_var}")
                return True

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
        # Update each variable's history (shift left and add new value)
        if temperature is not None:
            self.values["temperature"] = self.values["temperature"][1:] + [temperature]
        if humidity is not None:
            self.values["humidity"] = self.values["humidity"][1:] + [humidity]
        if pressure is not None:
            self.values["pressure"] = self.values["pressure"][1:] + [pressure]
        if light is not None:
            self.values["light"] = self.values["light"][1:] + [light]

    def display_text(self, variable: str, data: float, unit: str):
        """
        Display sensor data with a color gradient graph.
        
        Custom styling with cyan/blue on black theme:
        - Top bar shows sensor name and current value in cyan text on black
        - Bottom section shows a color gradient graph (cyan=high, dark blue=low)
        - A bright cyan-green line traces the actual values

        Args:
            variable: The variable name (temperature, humidity, pressure, light)
            data: The current sensor value
            unit: The unit string (°C, %, hPa, Lux)
        """
        # Create new image with black background
        img = Image.new("RGB", (self.width, self.height), color=self.bg_color)
        draw = ImageDraw.Draw(img)

        # Get the values for this variable and calculate scaling
        values = self.values[variable]
        vmin = min(values)
        vmax = max(values)
        
        # Normalize colors (0 to 1 scale)
        # Add 1 to avoid division by zero when all values are the same
        colours = [(v - vmin + 1) / (vmax - vmin + 1) for v in values]

        # Format the message for the top bar
        # Use abbreviated variable name (4 chars) for small display
        message = f"{variable[:4]}: {data:.1f} {unit}"

        # Draw the color gradient graph with cyan/blue theme
        for i in range(len(colours)):
            # Custom blue gradient: high values = bright cyan (hue 0.5), low values = dark blue (hue 0.6)
            # Saturation and value vary with the data
            intensity = colours[i]
            hue = 0.55 - (intensity * 0.1)  # Slight hue shift from blue to cyan
            sat = 0.8 + (intensity * 0.2)   # More saturated when higher
            val = 0.2 + (intensity * 0.6)   # Brighter when higher
            r, g, b = [int(x * 255.0) for x in colorsys.hsv_to_rgb(hue, sat, val)]
            
            # Draw a 1-pixel wide rectangle of colour from top_bar to bottom
            draw.rectangle((i, self.top_bar_height, i + 1, self.height), (r, g, b))
            
            # Draw a bright line graph overlaying the colors
            graph_height = self.height - self.top_bar_height
            line_y = self.height - (colours[i] * graph_height)
            draw.rectangle((i, line_y, i + 1, line_y + 1), self.graph_line_color)

        # Write the text at the top in cyan (on black background)
        draw.text((0, 0), message, font=self.font, fill=self.text_color)

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

        # Get current variable and its data
        variable = self.variables[self.current_mode]
        unit = self.units[variable]

        # Get the current value for the active mode
        if variable == "temperature" and temperature is not None:
            self.display_text(variable, temperature, unit)
        elif variable == "humidity" and humidity is not None:
            self.display_text(variable, humidity, unit)
        elif variable == "pressure" and pressure is not None:
            self.display_text(variable, pressure, unit)
        elif variable == "light" and light is not None:
            self.display_text(variable, light, unit)
        else:
            # No data available - show placeholder
            self._show_no_data(variable, unit)

    def _show_no_data(self, variable: str, unit: str):
        """Show a 'no data' screen for the current variable."""
        img = Image.new("RGB", (self.width, self.height), color=self.bg_color)
        draw = ImageDraw.Draw(img)
        message = f"{variable[:4]}: -- {unit}"
        draw.text((0, 0), message, font=self.font, fill=self.accent_color)
        draw.text((self.width // 2 - 30, self.height // 2), "No data", 
                  font=self.font_sm, fill=self.accent_color)
        self.display.display(img)

    def clear(self):
        """Clear the display."""
        img = Image.new('RGB', (self.width, self.height), color=(0, 0, 0))
        self.display.display(img)

    def show_startup_message(self, message: str):
        """Show a startup/status message."""
        img = Image.new('RGB', (self.width, self.height), color=self.bg_color)
        draw = ImageDraw.Draw(img)

        # Center the message in cyan
        draw.text((5, self.height // 2 - 10), message, font=self.font, fill=self.text_color)
        self.display.display(img)


def run_lcd_display_loop(update_interval_s: float = 0.25):
    """
    Main loop for updating LCD display with sensor readings and interactive mode switching.

    Args:
        update_interval_s: Seconds between display updates (default: 0.25s for snappy mode switching)
    """
    print("Initializing Enviro+ LCD display...")

    try:
        display = EnviroLCDDisplay(rotation=90, history_length=160)
        display.show_startup_message("Starting...")
        time.sleep(1)
    except Exception as e:
        print(f"Failed to initialize LCD display: {e}")
        return

    print("LCD display initialized successfully")
    print("Cover the proximity sensor to cycle between modes:")
    for i, var in enumerate(display.variables):
        print(f"  {i + 1}. {var.capitalize()} ({display.units[var]})")

    # Read from database instead of accessing sensor directly to avoid I2C conflicts
    print("Reading sensor data from database to avoid I2C bus conflicts")

    # Track consecutive errors
    error_count = 0
    max_errors = 5

    while True:
        try:
            # Check for mode switch via proximity sensor
            display.check_mode_switch()

            # Read latest sensor data from database (written by sensor logger process)
            data = io_funcs.fetch_log_data_range(limit=1)

            if data and len(data["time"]) > 0:
                temperature = data["temperature"][0]
                humidity = data["humidity"][0]
                pressure = data.get("pressure", [None])[0]
                light = data.get("light", [None])[0]
            else:
                temperature, humidity, pressure, light = None, None, None, None

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
                draw.text((5, 30), "Sensor Error", font=display.font, fill=(255, 50, 50))
                display.display.display(img)
            time.sleep(0.25)  # Quick retry
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
    # Update every 0.25 seconds for snappy mode switching
    run_lcd_display_loop(update_interval_s=0.25)
