#!/usr/bin/env python3
"""
Quick script to check the health of polling threads.
Run this when you suspect a sensor has stopped syncing.
"""
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

DB_PATH = Path("sensor_data.db")

def check_health():
    """Check last update time for each sensor."""
    if not DB_PATH.exists():
        print(f"Error: Database not found at {DB_PATH}")
        return
    
    print("=" * 80)
    print("Sensor Polling Health Check")
    print("=" * 80)
    print()
    
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        
        # Get all sensor tables
        cursor.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='table' AND name LIKE 'sensor_%'
            ORDER BY name
        """)
        tables = [row[0] for row in cursor.fetchall()]
        
        if not tables:
            print("No sensor tables found in database!")
            return
        
        now = datetime.now()
        
        for table in tables:
            # Extract sensor name from table name
            sensor_name = table.replace("sensor_", "").replace("_", " ").title()
            
            # Get last record timestamp
            cursor.execute(f"""
                SELECT timestamp, temperature, humidity 
                FROM {table} 
                ORDER BY id DESC 
                LIMIT 1
            """)
            row = cursor.fetchone()
            
            if row:
                last_timestamp_int = row[0]
                temp = row[1]
                hum = row[2]
                
                # Convert INTEGER timestamp (milliseconds) to datetime
                last_time = datetime.fromtimestamp(last_timestamp_int / 1000.0)
                time_since = now - last_time
                
                # Status indicators
                if time_since < timedelta(minutes=2):
                    status = "🟢 HEALTHY"
                elif time_since < timedelta(minutes=10):
                    status = "🟡 WARNING"
                else:
                    status = "🔴 STALLED"
                
                print(f"{status} {sensor_name}")
                print(f"  Last reading: {last_time.strftime('%Y-%m-%d %H:%M:%S')}")
                print(f"  Time since:   {format_timedelta(time_since)}")
                print(f"  Values:       {temp:.1f}°C, {hum:.1f}%")
                
                # Get record count
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                count = cursor.fetchone()[0]
                print(f"  Total records: {count:,}")
                print()
            else:
                print(f"🔴 EMPTY {sensor_name}")
                print(f"  No records in database!")
                print()
        
        # Get server metadata
        print("=" * 80)
        print("Server Metadata")
        print("=" * 80)
        cursor.execute("""
            SELECT sensor_name, last_update, status, last_error, total_records
            FROM sensor_metadata
            ORDER BY sensor_name
        """)
        
        for row in cursor.fetchall():
            sensor_name, last_update_int, status, last_error, total_records = row
            
            if last_update_int and last_update_int > 0:
                last_update_time = datetime.fromtimestamp(last_update_int / 1000.0)
                time_since = now - last_update_time
                print(f"\n{sensor_name}:")
                print(f"  Status: {status}")
                print(f"  Last metadata update: {last_update_time.strftime('%Y-%m-%d %H:%M:%S')} ({format_timedelta(time_since)} ago)")
                print(f"  Total records: {total_records:,}")
                if last_error:
                    print(f"  Last error: {last_error}")
            else:
                print(f"\n{sensor_name}:")
                print(f"  Status: {status}")
                print(f"  No metadata updates yet")


def format_timedelta(td: timedelta) -> str:
    """Format timedelta in human-readable format."""
    days = td.days
    hours, remainder = divmod(td.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    if seconds > 0 or not parts:
        parts.append(f"{seconds}s")
    
    return " ".join(parts)


if __name__ == "__main__":
    check_health()
