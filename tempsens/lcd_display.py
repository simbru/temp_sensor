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

import math
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

        # Display modes - dashboard first, then cycle through each sensor graph
        self.modes = ["dashboard", "temperature", "pressure", "humidity", "light"]
        self.units = {
            "temperature": "°C",
            "pressure": "hPa",
            "humidity": "%",
            "light": "Lux"
        }
        self.current_mode = 0

        # List of sensor variables (for graph modes, excluding dashboard)
        self.variables = ["temperature", "pressure", "humidity", "light"]

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
                self.current_mode %= len(self.modes)
                self.last_page_time = time.time()
                current_mode_name = self.modes[self.current_mode]
                print(f"Switched to mode: {current_mode_name}")
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

        # Draw min/max labels on the right edge of the graph area
        # These show the actual value range the colors represent
        max_label = f"{vmax:.1f}"
        min_label = f"{vmin:.1f}"
        # Position labels at top and bottom of graph area, right-aligned
        draw.text((self.width - 35, self.top_bar_height + 2), max_label, 
                  font=self.font_sm, fill=self.accent_color)
        draw.text((self.width - 35, self.height - 14), min_label, 
                  font=self.font_sm, fill=self.accent_color)

        # Display the image
        self.display.display(img)

    def _draw_thermometer_icon(self, draw: ImageDraw.Draw, x: int, y: int, 
                                temp: Optional[float], size: int = 16):
        """Draw a thermometer icon with fill level based on temperature."""
        # Color based on temperature
        if temp is None:
            fill_color = self.accent_color
            fill_pct = 0.3
        elif temp < 10:
            fill_color = (80, 120, 255)  # Cold blue
            fill_pct = 0.2
        elif temp < 20:
            fill_color = (0, 200, 255)  # Cool cyan
            fill_pct = 0.4
        elif temp < 25:
            fill_color = (0, 255, 180)  # Nice green-cyan
            fill_pct = 0.6
        elif temp < 30:
            fill_color = (255, 200, 0)  # Warm yellow
            fill_pct = 0.8
        else:
            fill_color = (255, 80, 80)  # Hot red
            fill_pct = 1.0

        # Draw thermometer body (vertical tube)
        tube_w = 4
        tube_h = size - 6
        tx = x + (size - tube_w) // 2
        ty = y
        draw.rectangle([tx, ty, tx + tube_w, ty + tube_h], outline=fill_color)
        
        # Draw bulb at bottom
        bulb_r = 4
        bx = x + size // 2
        by = y + size - bulb_r
        draw.ellipse([bx - bulb_r, by - bulb_r, bx + bulb_r, by + bulb_r], fill=fill_color)
        
        # Fill level inside tube
        fill_h = int((tube_h - 2) * fill_pct)
        if fill_h > 0:
            draw.rectangle([tx + 1, ty + tube_h - 1 - fill_h, tx + tube_w - 1, ty + tube_h - 1], 
                          fill=fill_color)

    def _draw_droplet_icon(self, draw: ImageDraw.Draw, x: int, y: int,
                           humidity: Optional[float], size: int = 16):
        """Draw a water droplet icon with fill based on humidity."""
        # Color based on humidity level
        if humidity is None:
            color = self.accent_color
            fill_pct = 0.3
        elif humidity < 30:
            color = (255, 150, 50)  # Dry orange
            fill_pct = 0.2
        elif humidity < 50:
            color = (0, 200, 255)  # Good cyan
            fill_pct = 0.5
        elif humidity < 70:
            color = (0, 255, 200)  # Nice green-cyan
            fill_pct = 0.7
        else:
            color = (80, 150, 255)  # Very humid blue
            fill_pct = 1.0

        # Draw droplet shape using polygon
        cx = x + size // 2
        # Droplet points: top point, curves down to round bottom
        points = [
            (cx, y + 2),           # Top point
            (cx - 5, y + 8),       # Left curve
            (cx - 4, y + 12),      # Left bottom
            (cx, y + 14),          # Bottom center
            (cx + 4, y + 12),      # Right bottom
            (cx + 5, y + 8),       # Right curve
        ]
        draw.polygon(points, outline=color)
        
        # Fill based on humidity (simple horizontal fill from bottom)
        fill_h = int(10 * fill_pct)
        if fill_h > 0:
            # Small filled ellipse at bottom of droplet
            draw.ellipse([cx - 3, y + 14 - fill_h, cx + 3, y + 14], fill=color)

    def _draw_pressure_icon(self, draw: ImageDraw.Draw, x: int, y: int,
                            pressure: Optional[float], size: int = 16):
        """Draw a barometer/weather icon based on pressure."""
        # Determine weather based on pressure
        if pressure is None:
            color = self.accent_color
            weather = "unknown"
        elif pressure < 1000:
            color = (150, 100, 255)  # Stormy purple
            weather = "storm"
        elif pressure < 1010:
            color = (100, 150, 255)  # Rainy blue
            weather = "rain"
        elif pressure < 1020:
            color = (0, 200, 255)  # Fair cyan
            weather = "fair"
        else:
            color = (0, 255, 180)  # High pressure green
            weather = "high"

        cx = x + size // 2
        cy = y + size // 2

        if weather == "storm":
            # Lightning bolt
            points = [(cx, y + 2), (cx - 3, cy + 2), (cx + 1, cy + 2), 
                     (cx - 2, y + size - 2)]
            draw.line(points, fill=color, width=2)
        elif weather == "rain":
            # Cloud with rain drops
            draw.arc([x + 2, y + 2, x + 10, y + 10], 0, 180, fill=color)
            draw.arc([x + 6, y + 2, x + 14, y + 10], 0, 180, fill=color)
            # Rain drops
            for dx in [4, 8, 12]:
                draw.line([(x + dx, y + 10), (x + dx - 1, y + 14)], fill=color)
        elif weather == "high":
            # Sun rays
            draw.ellipse([cx - 4, cy - 4, cx + 4, cy + 4], outline=color)
            for angle in range(0, 360, 45):
                rad = math.radians(angle)
                x1 = cx + int(5 * math.cos(rad))
                y1 = cy + int(5 * math.sin(rad))
                x2 = cx + int(7 * math.cos(rad))
                y2 = cy + int(7 * math.sin(rad))
                draw.line([(x1, y1), (x2, y2)], fill=color)
        else:
            # Fair weather - simple cloud
            draw.arc([x + 1, y + 4, x + 9, y + 12], 0, 180, fill=color)
            draw.arc([x + 5, y + 3, x + 14, y + 12], 0, 180, fill=color)
            draw.line([(x + 3, y + 10), (x + 12, y + 10)], fill=color)

    def _draw_light_icon(self, draw: ImageDraw.Draw, x: int, y: int,
                         light: Optional[float], size: int = 16):
        """Draw a light bulb icon with brightness indication."""
        # Brightness level and color
        if light is None:
            color = self.accent_color
            rays = False
        elif light < 10:
            color = (60, 80, 100)  # Very dim
            rays = False
        elif light < 100:
            color = (100, 150, 200)  # Dim
            rays = False
        elif light < 500:
            color = (0, 200, 255)  # Medium
            rays = True
        else:
            color = (0, 255, 200)  # Bright
            rays = True

        cx = x + size // 2
        
        # Bulb shape - ellipse for glass
        draw.ellipse([cx - 5, y + 1, cx + 5, y + 10], outline=color)
        
        # Screw base
        draw.rectangle([cx - 3, y + 10, cx + 3, y + 14], outline=color)
        
        # Fill bulb if light is on
        if light is not None and light > 50:
            draw.ellipse([cx - 4, y + 2, cx + 4, y + 9], fill=color)
        
        # Light rays if bright
        if rays:
            # Small dots around the bulb
            for dx, dy in [(-7, 4), (7, 4), (-5, -1), (5, -1)]:
                draw.point((cx + dx, y + 5 + dy), fill=color)

    def draw_dashboard(self, temperature: Optional[float], humidity: Optional[float],
                       pressure: Optional[float] = None, light: Optional[float] = None):
        """
        Draw a mini dashboard showing all sensor readings with icons.
        
        Layout (160x80 display):
        - 2x2 grid with icon + large value for each sensor
        - Icons change color/style based on reading levels
        
        Args:
            temperature: Temperature in Celsius
            humidity: Humidity percentage
            pressure: Atmospheric pressure in hPa
            light: Light level in lux
        """
        img = Image.new("RGB", (self.width, self.height), color=self.bg_color)
        draw = ImageDraw.Draw(img)

        # 2x2 grid layout
        # Top row: Temperature | Humidity
        # Bottom row: Pressure | Light
        icon_size = 16
        left_col = 4
        right_col = 84
        top_row = 4
        bottom_row = 42

        # ===== TEMPERATURE (top left) =====
        self._draw_thermometer_icon(draw, left_col, top_row, temperature, icon_size)
        if temperature is not None:
            temp_str = f"{temperature:.1f}°"
            draw.text((left_col + icon_size + 4, top_row), temp_str, 
                     font=self.font, fill=self.text_color)
        else:
            draw.text((left_col + icon_size + 4, top_row), "--°", 
                     font=self.font, fill=self.accent_color)

        # ===== HUMIDITY (top right) =====
        self._draw_droplet_icon(draw, right_col, top_row, humidity, icon_size)
        if humidity is not None:
            hum_str = f"{humidity:.0f}%"
            draw.text((right_col + icon_size + 4, top_row), hum_str, 
                     font=self.font, fill=self.text_color)
        else:
            draw.text((right_col + icon_size + 4, top_row), "--%", 
                     font=self.font, fill=self.accent_color)

        # ===== PRESSURE (bottom left) =====
        self._draw_pressure_icon(draw, left_col, bottom_row, pressure, icon_size)
        if pressure is not None:
            pres_str = f"{pressure:.0f}"
            draw.text((left_col + icon_size + 4, bottom_row - 2), pres_str, 
                     font=self.font, fill=self.text_color)
        else:
            draw.text((left_col + icon_size + 4, bottom_row - 2), "----", 
                     font=self.font, fill=self.accent_color)

        # ===== LIGHT (bottom right) =====
        self._draw_light_icon(draw, right_col, bottom_row, light, icon_size)
        if light is not None:
            if light >= 1000:
                light_str = f"{light/1000:.1f}k"
            else:
                light_str = f"{light:.0f}"
            draw.text((right_col + icon_size + 4, bottom_row - 2), light_str, 
                     font=self.font, fill=self.text_color)
        else:
            draw.text((right_col + icon_size + 4, bottom_row - 2), "----", 
                     font=self.font, fill=self.accent_color)

        # Subtle grid dividers
        mid_x = self.width // 2
        mid_y = self.height // 2
        # Vertical divider
        for y in range(8, self.height - 8):
            if y % 4 == 0:
                draw.point((mid_x - 2, y), fill=self.accent_color)
        # Horizontal divider  
        for x in range(8, self.width - 8):
            if x % 4 == 0:
                draw.point((x, mid_y), fill=self.accent_color)

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
        mode = self.modes[self.current_mode]

        # Dashboard mode - show all sensors
        if mode == "dashboard":
            self.draw_dashboard(temperature, humidity, pressure, light)
            return

        # Graph modes - show individual sensor with graph
        unit = self.units[mode]

        # Get the current value for the active mode
        if mode == "temperature" and temperature is not None:
            self.display_text(mode, temperature, unit)
        elif mode == "humidity" and humidity is not None:
            self.display_text(mode, humidity, unit)
        elif mode == "pressure" and pressure is not None:
            self.display_text(mode, pressure, unit)
        elif mode == "light" and light is not None:
            self.display_text(mode, light, unit)
        else:
            # No data available - show placeholder
            self._show_no_data(mode, unit)

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
    for i, mode in enumerate(display.modes):
        if mode == "dashboard":
            print(f"  {i + 1}. Dashboard (all sensors)")
        else:
            print(f"  {i + 1}. {mode.capitalize()} graph ({display.units[mode]})")

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
