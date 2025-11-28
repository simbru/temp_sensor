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
import pathlib
from typing import Optional, List, Dict
from PIL import Image, ImageDraw, ImageFont

from . import io_funcs


def _find_icons_path() -> Optional[pathlib.Path]:
    """
    Find the path to Pimoroni's enviroplus icons.
    
    The icons are in the examples/icons folder of the enviroplus-python repo.
    When installed via pip, they're typically in site-packages.
    """
    try:
        import enviroplus
        # Icons are in examples/icons relative to the package
        package_dir = pathlib.Path(enviroplus.__file__).parent.parent
        icons_path = package_dir / "examples" / "icons"
        if icons_path.exists():
            return icons_path
    except ImportError:
        pass
    
    # Fallback: check common locations
    fallback_paths = [
        pathlib.Path("/usr/local/lib/python3.11/dist-packages/examples/icons"),
        pathlib.Path.home() / ".local/lib/python3.11/site-packages/examples/icons",
    ]
    for p in fallback_paths:
        if p.exists():
            return p
    
    return None


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
        self.font = self._load_font(16)
        self.font_sm = self._load_font(10)
        self.font_lg = self._load_font(12)
        self.font_dashboard = self._load_font(32)  # Extra large for dashboard screen

        # Load icons from enviroplus package
        self.icons_path = _find_icons_path()
        self.icons: Dict[str, Image.Image] = {}
        self._load_icons()

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
        # Use None for empty slots so we know where real data starts
        self.values: Dict[str, List[Optional[float]]] = {}
        for v in self.variables:
            self.values[v] = [None] * self.width  # None = no data yet

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
        # Try Pimoroni's fonts.ttf package first (this is what their examples use)
        try:
            from fonts.ttf import RobotoMedium as UserFont
            font = ImageFont.truetype(UserFont, size)
            return font
        except (ImportError, OSError) as e:
            pass

        # Try common system font paths
        font_paths = [
            "/usr/share/fonts/truetype/roboto/unhinted/RobotoTTF/Roboto-Medium.ttf",
            "/usr/share/fonts/truetype/roboto/Roboto-Medium.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        ]

        for font_path in font_paths:
            try:
                return ImageFont.truetype(font_path, size)
            except (OSError, IOError):
                continue

        # Last resort - PIL's default (this is tiny and ignores size!)
        print(f"WARNING: Could not load any TrueType font, using default bitmap font (size {size} will be ignored)")
        return ImageFont.load_default()

    def _load_icons(self):
        """Load Pimoroni's icons from the enviroplus package."""
        if self.icons_path is None:
            print("WARNING: Could not find enviroplus icons path, using fallback drawn icons")
            return
        
        icon_files = [
            # Temperature
            "temperature.png",
            # Humidity - reactive based on level
            "humidity.png",
            "humidity-good.png",
            "humidity-bad.png",
            # Light - reactive based on brightness
            "bulb-dark.png",
            "bulb-dim.png",
            "bulb-light.png",
            "bulb-bright.png",
            # Weather/Pressure - reactive based on pressure
            "weather-storm.png",
            "weather-rain.png",
            "weather-change.png",
            "weather-fair.png",
            "weather-dry.png",
        ]
        
        for icon_file in icon_files:
            icon_path = self.icons_path / icon_file
            if icon_path.exists():
                try:
                    self.icons[icon_file.replace(".png", "")] = Image.open(icon_path)
                except Exception as e:
                    print(f"WARNING: Could not load icon {icon_file}: {e}")
        
        if self.icons:
            print(f"Loaded {len(self.icons)} icons from {self.icons_path}")

    def _get_humidity_icon(self, humidity: Optional[float]) -> Optional[Image.Image]:
        """Get the appropriate humidity icon based on level."""
        if humidity is None:
            return self.icons.get("humidity")
        elif 40 < humidity < 60:
            return self.icons.get("humidity-good", self.icons.get("humidity"))
        else:
            return self.icons.get("humidity-bad", self.icons.get("humidity"))

    def _get_light_icon(self, light: Optional[float]) -> Optional[Image.Image]:
        """Get the appropriate light/bulb icon based on lux level."""
        if light is None:
            return self.icons.get("bulb-dim")
        elif light < 50:
            return self.icons.get("bulb-dark", self.icons.get("bulb-dim"))
        elif light < 100:
            return self.icons.get("bulb-dim")
        elif light < 500:
            return self.icons.get("bulb-light", self.icons.get("bulb-dim"))
        else:
            return self.icons.get("bulb-bright", self.icons.get("bulb-light"))

    def _get_pressure_icon(self, pressure: Optional[float]) -> Optional[Image.Image]:
        """Get the appropriate weather icon based on pressure."""
        if pressure is None:
            return self.icons.get("weather-fair")
        elif pressure < 970:
            return self.icons.get("weather-storm", self.icons.get("weather-rain"))
        elif pressure < 990:
            return self.icons.get("weather-rain", self.icons.get("weather-change"))
        elif pressure < 1010:
            return self.icons.get("weather-change", self.icons.get("weather-fair"))
        elif pressure < 1030:
            return self.icons.get("weather-fair")
        else:
            return self.icons.get("weather-dry", self.icons.get("weather-fair"))

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
        Display sensor data with a line graph.
        
        Clean styling with cyan on black theme:
        - Top bar shows sensor name and current value in cyan text
        - Bottom section shows a simple line graph on black background
        - Empty/no-data regions are left black

        Args:
            variable: The variable name (temperature, humidity, pressure, light)
            data: The current sensor value
            unit: The unit string (°C, %, hPa, Lux)
        """
        # Create new image with black background
        img = Image.new("RGB", (self.width, self.height), color=self.bg_color)
        draw = ImageDraw.Draw(img)

        # Get the values for this variable, filtering out None (no data yet)
        all_values = self.values[variable]
        real_values = [v for v in all_values if v is not None]
        
        # If no real data yet, just show the text
        if not real_values:
            message = f"{data:.1f} {unit}"
            draw.text((0, 0), message, font=self.font, fill=self.text_color)
            draw.text((self.width // 2 - 30, self.height // 2), "waiting...", 
                      font=self.font_sm, fill=self.accent_color)
            self.display.display(img)
            return
        
        vmin = min(real_values)
        vmax = max(real_values)
        value_range = vmax - vmin if vmax != vmin else 1.0  # Avoid division by zero

        # Format the message for the top bar - just value and unit, no label needed
        message = f"{data:.1f} {unit}"

        graph_height = self.height - self.top_bar_height
        line_width = 2  # Line thickness
        
        # Collect line points where we have data
        line_points = []
        for i in range(len(all_values)):
            v = all_values[i]
            if v is not None:
                intensity = (v - vmin) / value_range if value_range > 0 else 0.5
                line_y = self.height - int(intensity * graph_height) - 1
                line_points.append((i, line_y))

        # Draw continuous line connecting all points
        if len(line_points) >= 2:
            draw.line(line_points, fill=self.text_color, width=line_width)
        elif len(line_points) == 1:
            # Just one point - draw a small dot
            x, y = line_points[0]
            draw.ellipse((x-1, y-1, x+1, y+1), fill=self.text_color)

        # Write the text at the top in cyan
        draw.text((0, 0), message, font=self.font, fill=self.text_color)

        # Draw min/max labels on the right edge of the graph area
        max_label = f"{vmax:.1f}"
        min_label = f"{vmin:.1f}"
        draw.text((self.width - 35, self.top_bar_height + 2), max_label, 
                  font=self.font_sm, fill=(255, 255, 255))
        draw.text((self.width - 35, self.height - 14), min_label, 
                  font=self.font_sm, fill=(255, 255, 255))

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
                import math
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
        
        Uses Pimoroni's reactive icons when available (humidity, light, pressure
        icons change based on sensor values), falls back to drawn icons if not.
        
        Layout (160x80 display):
        - 2x2 grid with icon + large value for each sensor
        - Icons change based on reading levels
        
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
        text_offset = icon_size + 6

        # Helper to paste icon with transparency
        def paste_icon(icon: Optional[Image.Image], x: int, y: int):
            if icon is not None:
                # Convert to RGBA if needed and paste with transparency
                if icon.mode == 'RGBA':
                    img.paste(icon, (x, y), mask=icon)
                else:
                    img.paste(icon, (x, y))
                return True
            return False

        # ===== TEMPERATURE (top left) =====
        temp_icon = self.icons.get("temperature")
        if not paste_icon(temp_icon, left_col, top_row):
            self._draw_thermometer_icon(draw, left_col, top_row, temperature, icon_size)
        
        if temperature is not None:
            temp_str = f"{temperature:.1f}°"
            draw.text((left_col + text_offset, top_row), temp_str, 
                     font=self.font, fill=self.text_color)
        else:
            draw.text((left_col + text_offset, top_row), "--°", 
                     font=self.font, fill=self.accent_color)

        # ===== HUMIDITY (top right) - reactive icon =====
        hum_icon = self._get_humidity_icon(humidity)
        if not paste_icon(hum_icon, right_col, top_row):
            self._draw_droplet_icon(draw, right_col, top_row, humidity, icon_size)
        
        if humidity is not None:
            hum_str = f"{humidity:.0f}%"
            draw.text((right_col + text_offset, top_row), hum_str, 
                     font=self.font, fill=self.text_color)
        else:
            draw.text((right_col + text_offset, top_row), "--%", 
                     font=self.font, fill=self.accent_color)

        # ===== PRESSURE (bottom left) - reactive weather icon =====
        pres_icon = self._get_pressure_icon(pressure)
        if not paste_icon(pres_icon, left_col, bottom_row):
            self._draw_pressure_icon(draw, left_col, bottom_row, pressure, icon_size)
        
        if pressure is not None:
            pres_str = f"{pressure:.0f}"
            draw.text((left_col + text_offset, bottom_row - 2), pres_str, 
                     font=self.font, fill=self.text_color)
        else:
            draw.text((left_col + text_offset, bottom_row - 2), "----", 
                     font=self.font, fill=self.accent_color)

        # ===== LIGHT (bottom right) - reactive bulb icon =====
        light_icon = self._get_light_icon(light)
        if not paste_icon(light_icon, right_col, bottom_row):
            self._draw_light_icon(draw, right_col, bottom_row, light, icon_size)
        
        if light is not None:
            if light >= 1000:
                light_str = f"{light/1000:.1f}k"
            else:
                light_str = f"{light:.0f}"
            draw.text((right_col + text_offset, bottom_row - 2), light_str, 
                     font=self.font, fill=self.text_color)
        else:
            draw.text((right_col + text_offset, bottom_row - 2), "----", 
                     font=self.font, fill=self.accent_color)

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
