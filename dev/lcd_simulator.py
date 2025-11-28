#!/usr/bin/env python3
"""
LCD Display Simulator for remote development without hardware.

This module provides mock implementations of the ST7735 display and LTR559 proximity
sensor, allowing you to develop and test LCD display code without the actual hardware.

Usage:
    1. Run directly to test the display with simulated sensor data:
       python dev/lcd_simulator.py

    2. Import and use as a drop-in replacement:
       from dev.lcd_simulator import MockST7735, MockLTR559

Features:
    - Saves each frame as PNG to dev/lcd_frames/
    - Optional ASCII art preview in terminal
    - Simulates proximity sensor for mode switching (keyboard input)
    - Can replay saved frames as animation

Author: Development tool for temp_sensor project
"""

import os
import sys
import time
import threading
from pathlib import Path
from typing import Optional, Tuple
from PIL import Image

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


class MockST7735:
    """
    Mock ST7735 LCD display that saves frames to files instead of hardware.
    
    Drop-in replacement for st7735.ST7735 for development without hardware.
    """
    
    def __init__(self, port=0, cs=1, dc="GPIO9", backlight="GPIO12", 
                 rotation=90, spi_speed_hz=10000000,
                 save_frames=True, show_ascii=False, frame_dir=None):
        """
        Initialize mock display.
        
        Args:
            port, cs, dc, backlight, spi_speed_hz: Ignored (hardware params)
            rotation: Display rotation (affects width/height)
            save_frames: Whether to save each frame as PNG
            show_ascii: Whether to print ASCII art preview to terminal
            frame_dir: Directory to save frames (default: dev/lcd_frames)
        """
        self.rotation = rotation
        self.save_frames = save_frames
        self.show_ascii = show_ascii
        
        # Set dimensions based on rotation
        if rotation in [90, 270]:
            self._width = 160
            self._height = 80
        else:
            self._width = 80
            self._height = 160
            
        self.frame_count = 0
        self.last_image: Optional[Image.Image] = None
        
        # Setup frame directory
        if frame_dir is None:
            self.frame_dir = Path(__file__).parent / "lcd_frames"
        else:
            self.frame_dir = Path(frame_dir)
        
        if save_frames:
            self.frame_dir.mkdir(exist_ok=True)
        
        print(f"[MockST7735] Initialized {self._width}x{self._height} display (rotation={rotation})")
        if save_frames:
            print(f"[MockST7735] Saving frames to: {self.frame_dir}/latest.png")
    
    def begin(self):
        """Initialize display (no-op for mock)."""
        print("[MockST7735] Display initialized")
    
    @property
    def width(self) -> int:
        return self._width
    
    @property
    def height(self) -> int:
        return self._height
    
    def display(self, image: Image.Image):
        """
        'Display' an image by saving it and optionally showing ASCII preview.
        
        Args:
            image: PIL Image to display
        """
        self.last_image = image.copy()
        self.frame_count += 1
        
        # Save frame as PNG (just latest.png, overwritten each time)
        if self.save_frames:
            # Scale up for easier viewing (4x)
            scaled = image.resize((self._width * 4, self._height * 4), Image.NEAREST)
            
            # Save as 'latest.png' - overwrites each time
            latest_path = self.frame_dir / "latest.png"
            scaled.save(latest_path)
        
        # Show ASCII preview
        if self.show_ascii:
            self._print_ascii(image)
    
    def _print_ascii(self, image: Image.Image, width_chars: int = 80):
        """
        Print ASCII art representation of the image.
        
        Args:
            image: PIL Image to convert
            width_chars: Width in characters for ASCII output
        """
        # Calculate height to maintain aspect ratio (chars are ~2x taller than wide)
        aspect = self._height / self._width
        height_chars = int(width_chars * aspect * 0.5)
        
        # Resize image for ASCII
        ascii_img = image.resize((width_chars, height_chars))
        ascii_img = ascii_img.convert('L')  # Convert to grayscale
        
        # ASCII characters from dark to light
        chars = " .:-=+*#%@"
        
        # Clear screen and move cursor to top
        print("\033[2J\033[H", end="")
        print(f"┌{'─' * width_chars}┐")
        
        for y in range(height_chars):
            line = "│"
            for x in range(width_chars):
                pixel = ascii_img.getpixel((x, y))
                char_idx = int(pixel / 256 * len(chars))
                char_idx = min(char_idx, len(chars) - 1)
                line += chars[char_idx]
            line += "│"
            print(line)
        
        print(f"└{'─' * width_chars}┘")
        print(f"Frame: {self.frame_count}")
    
    def set_backlight(self, value: bool):
        """Set backlight state (no-op for mock)."""
        pass


class MockLTR559:
    """
    Mock LTR559 proximity/light sensor with keyboard control.
    
    Simulates proximity detection via keyboard input or automated triggers.
    """
    
    def __init__(self):
        self._proximity = 0
        self._lux = 500.0
        self._trigger_proximity = False
        self._keyboard_thread: Optional[threading.Thread] = None
        self._running = False
        
        print("[MockLTR559] Proximity sensor initialized")
        print("[MockLTR559] Press ENTER to simulate proximity trigger (mode switch)")
    
    def start_keyboard_listener(self):
        """Start background thread to listen for keyboard input."""
        if self._keyboard_thread is not None:
            return
            
        self._running = True
        self._keyboard_thread = threading.Thread(target=self._keyboard_loop, daemon=True)
        self._keyboard_thread.start()
    
    def _keyboard_loop(self):
        """Background thread that listens for Enter key to trigger proximity."""
        import select
        import sys
        
        while self._running:
            try:
                # Non-blocking check for input
                if sys.stdin in select.select([sys.stdin], [], [], 0.1)[0]:
                    sys.stdin.readline()
                    self._trigger_proximity = True
                    print("[MockLTR559] Proximity triggered! (mode switch)")
            except Exception:
                # Fallback for Windows or other issues
                time.sleep(0.1)
    
    def stop_keyboard_listener(self):
        """Stop the keyboard listener thread."""
        self._running = False
        if self._keyboard_thread:
            self._keyboard_thread.join(timeout=1.0)
            self._keyboard_thread = None
    
    def trigger_proximity(self):
        """Manually trigger a proximity event (for scripted testing)."""
        self._trigger_proximity = True
    
    def get_proximity(self) -> int:
        """
        Get proximity reading.
        
        Returns high value (2000) briefly when triggered, otherwise 0.
        """
        if self._trigger_proximity:
            self._trigger_proximity = False
            return 2000
        return 0
    
    def get_lux(self) -> float:
        """Get current light level in lux."""
        return self._lux
    
    def set_lux(self, value: float):
        """Set simulated light level."""
        self._lux = value


def patch_hardware_imports():
    """
    Patch the hardware imports so lcd_display.py uses our mocks.
    
    Call this before importing lcd_display module.
    """
    import sys
    from unittest.mock import MagicMock
    
    # Create mock modules
    mock_st7735 = MagicMock()
    mock_st7735.ST7735 = MockST7735
    
    mock_ltr559 = MagicMock()
    mock_ltr559.LTR559 = MockLTR559
    
    # Inject into sys.modules
    sys.modules['st7735'] = mock_st7735
    sys.modules['ltr559'] = mock_ltr559
    
    print("[Simulator] Hardware imports patched with mocks")


def run_simulation(duration: float = 30.0, update_interval: float = 0.5):
    """
    Run a simulation of the LCD display with fake sensor data.
    
    Args:
        duration: How long to run the simulation (seconds)
        update_interval: Time between display updates (seconds)
    """
    import math
    import random
    
    # Patch hardware imports before importing lcd_display
    patch_hardware_imports()
    
    # Now we can import lcd_display (it will use our mocks)
    from tempsens.lcd_display import EnviroLCDDisplay
    
    print("\n" + "=" * 60)
    print("LCD Display Simulator")
    print("=" * 60)
    print(f"Running for {duration} seconds")
    print(f"Frames saved to: dev/lcd_frames/")
    print(f"View latest frame: dev/lcd_frames/latest.png")
    print("Press Ctrl+C to stop")
    print("=" * 60 + "\n")
    
    # Initialize display
    lcd = EnviroLCDDisplay(rotation=90)
    
    # Start keyboard listener for proximity simulation
    lcd.ltr559.start_keyboard_listener()
    
    # Simulate sensor data
    start_time = time.time()
    base_temp = 22.0
    base_humidity = 45.0
    base_pressure = 1013.0
    base_light = 300.0
    
    try:
        while time.time() - start_time < duration:
            elapsed = time.time() - start_time
            
            # Generate varying sensor values (sinusoidal + noise)
            temperature = base_temp + 3 * math.sin(elapsed / 10) + random.uniform(-0.5, 0.5)
            humidity = base_humidity + 10 * math.sin(elapsed / 15) + random.uniform(-2, 2)
            pressure = base_pressure + 5 * math.sin(elapsed / 20) + random.uniform(-1, 1)
            light = max(0, base_light + 200 * math.sin(elapsed / 8) + random.uniform(-20, 20))
            
            # Clamp values to realistic ranges
            humidity = max(0, min(100, humidity))
            
            # Check for mode switch
            lcd.check_mode_switch()
            
            # Update display
            lcd.update_display(
                temperature=temperature,
                humidity=humidity,
                pressure=pressure,
                light=light
            )
            
            # Print status
            mode = lcd.modes[lcd.current_mode]
            print(f"\r[{elapsed:6.1f}s] Mode: {mode:12s} | "
                  f"T:{temperature:5.1f}°C H:{humidity:4.0f}% P:{pressure:6.0f}hPa L:{light:5.0f}lux",
                  end="", flush=True)
            
            time.sleep(update_interval)
            
    except KeyboardInterrupt:
        print("\n\nSimulation stopped by user")
    finally:
        lcd.ltr559.stop_keyboard_listener()
    
    print(f"\nSimulation complete! {lcd.display.frame_count} frames saved.")
    print(f"View frames in: {lcd.display.frame_dir}")


def create_animation(frame_dir: Optional[str] = None, output: str = "lcd_animation.gif", 
                     fps: int = 4):
    """
    Create an animated GIF from saved frames.
    
    Args:
        frame_dir: Directory containing frame_*.png files
        output: Output GIF filename
        fps: Frames per second for animation
    """
    if frame_dir is None:
        frame_dir = Path(__file__).parent / "lcd_frames"
    else:
        frame_dir = Path(frame_dir)
    
    frames = sorted(frame_dir.glob("frame_*.png"))
    
    if not frames:
        print(f"No frames found in {frame_dir}")
        return
    
    print(f"Creating animation from {len(frames)} frames...")
    
    images = [Image.open(f) for f in frames]
    
    output_path = frame_dir / output
    images[0].save(
        output_path,
        save_all=True,
        append_images=images[1:],
        duration=1000 // fps,
        loop=0
    )
    
    print(f"Animation saved to: {output_path}")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="LCD Display Simulator")
    parser.add_argument("--duration", "-d", type=float, default=60.0,
                       help="Simulation duration in seconds (default: 60)")
    parser.add_argument("--interval", "-i", type=float, default=0.25,
                       help="Update interval in seconds (default: 0.25)")
    parser.add_argument("--animate", "-a", action="store_true",
                       help="Create animation from existing frames instead of running simulation")
    parser.add_argument("--fps", type=int, default=4,
                       help="FPS for animation (default: 4)")
    
    args = parser.parse_args()
    
    if args.animate:
        create_animation(fps=args.fps)
    else:
        run_simulation(duration=args.duration, update_interval=args.interval)
