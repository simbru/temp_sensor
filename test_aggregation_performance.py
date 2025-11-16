#!/usr/bin/env python3
"""
Performance test for multi-sensor aggregation.
Tests polling efficiency with varying numbers of sensors.
"""
import time
import sys
from pathlib import Path
from server.api_client import MultiSensorClient
from server.data_aggregator import DataAggregator


def test_polling_performance(num_sensors: int = 3, num_polls: int = 3):
    """
    Test polling performance with N sensors.
    
    Args:
        num_sensors: Number of sensors to simulate
        num_polls: Number of polling cycles to run
    """
    print(f"\n{'='*70}")
    print(f"Performance Test: {num_sensors} sensors, {num_polls} polling cycles")
    print(f"{'='*70}\n")
    
    # Create sensor configs
    sensor_configs = [
        {
            "name": f"Test Sensor {i+1}",
            "url": f"http://localhost:{5001+i}"
        }
        for i in range(num_sensors)
    ]
    
    # Initialize client and aggregator
    client = MultiSensorClient(sensor_configs)
    db_path = f"test_perf_{num_sensors}sensors.db"
    aggregator = DataAggregator(client, db_path=db_path, poll_interval=10)
    
    print(f"Testing with {num_sensors} sensor(s)...")
    print(f"Note: Sensors may not be running, measuring request handling speed\n")
    
    # Measure poll_once performance
    poll_times = []
    
    for i in range(num_polls):
        print(f"Poll cycle {i+1}/{num_polls}...")
        start_time = time.time()
        
        try:
            aggregator.poll_once()
        except Exception as e:
            print(f"  Warning: Poll failed (expected if sensors not running): {e}")
        
        elapsed = time.time() - start_time
        poll_times.append(elapsed)
        print(f"  Completed in {elapsed:.3f}s\n")
    
    # Calculate statistics
    avg_time = sum(poll_times) / len(poll_times)
    min_time = min(poll_times)
    max_time = max(poll_times)
    
    print(f"\n{'='*70}")
    print(f"Results for {num_sensors} sensor(s):")
    print(f"{'='*70}")
    print(f"Average poll time: {avg_time:.3f}s")
    print(f"Min poll time:     {min_time:.3f}s")
    print(f"Max poll time:     {max_time:.3f}s")
    print(f"Time per sensor:   {avg_time/num_sensors:.3f}s (average)")
    print(f"\nNote: With parallel polling, time should scale sub-linearly with sensor count")
    print(f"      (e.g., 10 sensors should take < 10x the time of 1 sensor)")
    print(f"{'='*70}\n")
    
    # Cleanup
    Path(db_path).unlink(missing_ok=True)
    
    return {
        "num_sensors": num_sensors,
        "avg_time": avg_time,
        "min_time": min_time,
        "max_time": max_time,
        "time_per_sensor": avg_time / num_sensors
    }


def main():
    """Run performance tests with different sensor counts."""
    print("\n" + "="*70)
    print("Multi-Sensor Aggregation Performance Test")
    print("="*70)
    print("\nThis test measures polling efficiency improvements from:")
    print("  1. Parallel polling with ThreadPoolExecutor")
    print("  2. Connection pooling with requests.Session")
    print("  3. Optimized gap detection (single HTTP request per sensor)")
    
    # Test with varying sensor counts
    test_configs = [
        (1, 3),   # 1 sensor, 3 polls
        (3, 3),   # 3 sensors, 3 polls
        (5, 3),   # 5 sensors, 3 polls
        (10, 2),  # 10 sensors, 2 polls
    ]
    
    results = []
    for num_sensors, num_polls in test_configs:
        result = test_polling_performance(num_sensors, num_polls)
        results.append(result)
        time.sleep(1)  # Brief pause between tests
    
    # Print summary
    print("\n" + "="*70)
    print("SUMMARY: Scalability Analysis")
    print("="*70)
    print(f"{'Sensors':<10} {'Avg Time':<12} {'Time/Sensor':<15} {'Efficiency'}")
    print("-" * 70)
    
    baseline_time_per_sensor = results[0]["time_per_sensor"]
    
    for r in results:
        efficiency = (baseline_time_per_sensor / r["time_per_sensor"]) * 100 if r["time_per_sensor"] > 0 else 0
        print(f"{r['num_sensors']:<10} {r['avg_time']:<12.3f} {r['time_per_sensor']:<15.3f} {efficiency:.1f}%")
    
    print("\nInterpretation:")
    print("  - Efficiency > 80%: Good parallel scaling")
    print("  - Efficiency 50-80%: Moderate scaling, some overhead")
    print("  - Efficiency < 50%: Poor scaling, sequential bottleneck")
    print("="*70)


if __name__ == "__main__":
    main()
