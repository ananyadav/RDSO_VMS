#!/usr/bin/env python3
"""
Network Diagnostic Tool for CCTV RTSP Connectivity Issues
Usage: python scripts/diagnose_network.py [camera_id]
"""

import asyncio
import sys
import os
import subprocess
import socket
import urllib.parse
from typing import List, Dict, Optional

# Add the app directory to the Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.core.database import camera_collection
from bson import ObjectId


def ping_host(host: str, timeout: int = 3) -> bool:
    """Check if host is reachable via ping."""
    try:
        # Use ping command (works on both Linux and Windows)
        if sys.platform == "win32":
            result = subprocess.run(
                ["ping", "-n", "1", "-w", str(timeout * 1000), host],
                capture_output=True,
                timeout=timeout + 1
            )
        else:
            result = subprocess.run(
                ["ping", "-c", "1", "-W", str(timeout), host],
                capture_output=True,
                timeout=timeout + 1
            )
        return result.returncode == 0
    except Exception as e:
        return False


def check_port(host: str, port: int, timeout: int = 3) -> bool:
    """Check if a TCP port is open."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except Exception as e:
        return False


def test_rtsp_connection(rtsp_url: str, timeout: int = 10) -> Dict:
    """Test RTSP connection using ffprobe."""
    result = {
        "success": False,
        "error": None,
        "codec": None,
        "resolution": None
    }
    
    try:
        cmd = [
            "ffprobe",
            "-v", "error",
            "-rtsp_transport", "tcp",
            "-timeout", "10000000",  # 10s in microseconds
            "-print_format", "json",
            "-show_streams",
            rtsp_url,
        ]
        
        proc_result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout + 2
        )
        
        if proc_result.returncode == 0:
            import json
            data = json.loads(proc_result.stdout or "{}")
            for stream in data.get("streams", []):
                if stream.get("codec_type") == "video":
                    result["success"] = True
                    result["codec"] = stream.get("codec_name")
                    result["resolution"] = f"{stream.get('width')}x{stream.get('height')}"
                    break
        else:
            error_msg = (proc_result.stderr or proc_result.stdout or "").strip()
            result["error"] = error_msg[:200]
            
    except subprocess.TimeoutExpired:
        result["error"] = "Connection timeout"
    except Exception as e:
        result["error"] = str(e)
    
    return result


def get_server_network_info() -> Dict:
    """Get server's network configuration."""
    info = {
        "hostname": socket.gethostname(),
        "ip_addresses": [],
        "interfaces": []
    }
    
    try:
        import socket
        import platform
        
        # Get hostname
        info["hostname"] = socket.gethostname()
        
        # Get IP addresses
        hostname = socket.gethostname()
        info["ip_addresses"].append(socket.gethostbyname(hostname))
        
        # Try to get all IP addresses
        try:
            import psutil
            for interface, addrs in psutil.net_if_addrs().items():
                for addr in addrs:
                    if addr.family == socket.AF_INET:
                        info["interfaces"].append({
                            "interface": interface,
                            "ip": addr.address,
                            "netmask": addr.netmask
                        })
        except ImportError:
            pass
            
    except Exception as e:
        info["error"] = str(e)
    
    return info


async def diagnose_camera(camera_id: str) -> Dict:
    """Diagnose connectivity for a specific camera."""
    print(f"\n{'='*70}")
    print(f"Diagnosing Camera: {camera_id}")
    print(f"{'='*70}\n")
    
    try:
        cam_oid = ObjectId(camera_id)
        camera_doc = await camera_collection.find_one({"_id": cam_oid})
        
        if not camera_doc:
            print(f"❌ Camera {camera_id} not found in database")
            return {"success": False, "error": "Camera not found"}
        
        ip_address = camera_doc.get("ip_address", "").strip()
        port = camera_doc.get("port", 554)
        username = camera_doc.get("username", "admin")
        password = camera_doc.get("password", "")
        name = camera_doc.get("name", "Unknown")
        
        print(f"Camera Name: {name}")
        print(f"IP Address: {ip_address}")
        print(f"Port: {port}")
        print(f"Username: {username}")
        print()
        
        results = {
            "camera_id": camera_id,
            "camera_name": name,
            "ip_address": ip_address,
            "port": port,
            "tests": {}
        }
        
        # Test 1: Ping
        print("Test 1: Ping Test")
        print("-" * 70)
        ping_success = ping_host(ip_address)
        if ping_success:
            print(f"✅ Ping successful - Camera is reachable")
            results["tests"]["ping"] = {"success": True}
        else:
            print(f"❌ Ping failed - Camera is NOT reachable")
            print(f"   This indicates a network routing or connectivity issue")
            results["tests"]["ping"] = {"success": False, "error": "Host unreachable"}
        print()
        
        # Test 2: Port Check
        print("Test 2: RTSP Port (554) Check")
        print("-" * 70)
        port_open = check_port(ip_address, port)
        if port_open:
            print(f"✅ Port {port} is open and accessible")
            results["tests"]["port"] = {"success": True}
        else:
            print(f"❌ Port {port} is NOT accessible")
            print(f"   Possible causes:")
            print(f"   - Firewall blocking RTSP port")
            print(f"   - Camera RTSP service not running")
            print(f"   - Network routing issue")
            results["tests"]["port"] = {"success": False, "error": "Port not accessible"}
        print()
        
        # Test 3: RTSP Connection
        if ping_success and port_open:
            print("Test 3: RTSP Connection Test")
            print("-" * 70)
            password_encoded = urllib.parse.quote(str(password).strip(), safe="")
            username_encoded = urllib.parse.quote(username, safe="")
            
            # Try primary RTSP path
            model = (camera_doc.get("model") or "").lower()
            channel = str(camera_doc.get("recording_channel") or "102").strip()
            
            if "hikvision" in model or "hik" in model:
                rtsp_path = f"/Streaming/Channels/{channel}"
            elif "dahua" in model:
                subtype = "1" if channel.endswith("2") else "0"
                rtsp_path = f"/cam/realmonitor?channel=1&subtype={subtype}"
            elif "axis" in model:
                rtsp_path = "/axis-media/media.amp"
            else:
                rtsp_path = f"/Streaming/Channels/{channel}"
            
            rtsp_url = f"rtsp://{username_encoded}:{password_encoded}@{ip_address}:{port}{rtsp_path}"
            print(f"Testing RTSP URL: rtsp://{username_encoded}:***@{ip_address}:{port}{rtsp_path}")
            
            rtsp_result = test_rtsp_connection(rtsp_url)
            results["tests"]["rtsp"] = rtsp_result
            
            if rtsp_result["success"]:
                print(f"✅ RTSP connection successful!")
                print(f"   Codec: {rtsp_result.get('codec', 'Unknown')}")
                print(f"   Resolution: {rtsp_result.get('resolution', 'Unknown')}")
            else:
                print(f"❌ RTSP connection failed")
                error = rtsp_result.get("error", "Unknown error")
                print(f"   Error: {error}")
                
                if "cannot assign requested address" in error.lower():
                    print(f"\n   🔍 DIAGNOSIS: Network connectivity issue")
                    print(f"   This error typically means:")
                    print(f"   1. Server and camera are on different networks/subnets")
                    print(f"   2. Network routing is not configured correctly")
                    print(f"   3. Firewall is blocking the connection")
                    print(f"   4. Server's network interface is misconfigured")
        else:
            print("Test 3: RTSP Connection Test - SKIPPED (prerequisites failed)")
            results["tests"]["rtsp"] = {"success": False, "error": "Prerequisites failed"}
        print()
        
        # Summary
        print("Summary")
        print("-" * 70)
        all_tests = [results["tests"].get("ping", {}).get("success"),
                    results["tests"].get("port", {}).get("success"),
                    results["tests"].get("rtsp", {}).get("success")]
        
        if all(all_tests):
            print("✅ All tests passed - Camera should be working")
            results["success"] = True
        elif results["tests"].get("ping", {}).get("success") == False:
            print("❌ Network connectivity issue - Camera is not reachable")
            print("\n   RECOMMENDATIONS:")
            print("   1. Verify camera IP address is correct")
            print("   2. Check if server and cameras are on the same network/subnet")
            print("   3. Check network routing tables")
            print("   4. Verify firewall rules allow traffic to camera subnet")
            results["success"] = False
        elif results["tests"].get("port", {}).get("success") == False:
            print("❌ Port accessibility issue - RTSP port is blocked")
            print("\n   RECOMMENDATIONS:")
            print("   1. Check firewall rules (allow port 554)")
            print("   2. Verify camera RTSP service is running")
            print("   3. Check if camera is accessible from another machine")
            results["success"] = False
        else:
            print("⚠️  Partial connectivity - Some issues detected")
            results["success"] = False
        
        return results
        
    except Exception as e:
        print(f"❌ Error diagnosing camera: {e}")
        return {"success": False, "error": str(e)}


async def diagnose_all_cameras():
    """Diagnose all cameras in the database."""
    print(f"\n{'='*70}")
    print("Network Diagnostic Tool - All Cameras")
    print(f"{'='*70}\n")
    
    # Get server network info
    print("Server Network Information")
    print("-" * 70)
    server_info = get_server_network_info()
    print(f"Hostname: {server_info['hostname']}")
    if server_info.get("interfaces"):
        print("Network Interfaces:")
        for iface in server_info["interfaces"]:
            print(f"  - {iface['interface']}: {iface['ip']} (netmask: {iface['netmask']})")
    elif server_info.get("ip_addresses"):
        print(f"IP Address: {server_info['ip_addresses'][0]}")
    print()
    
    # Get all cameras
    cameras = await camera_collection.find({}).to_list(length=None)
    
    if not cameras:
        print("No cameras found in database")
        return
    
    print(f"Found {len(cameras)} camera(s) in database\n")
    
    results = []
    for camera in cameras:
        camera_id = str(camera["_id"])
        result = await diagnose_camera(camera_id)
        results.append(result)
    
    # Overall summary
    print(f"\n{'='*70}")
    print("Overall Summary")
    print(f"{'='*70}\n")
    
    successful = sum(1 for r in results if r.get("success"))
    total = len(results)
    
    print(f"Total Cameras: {total}")
    print(f"Fully Working: {successful}")
    print(f"Issues Detected: {total - successful}")
    print()
    
    if successful < total:
        print("⚠️  Some cameras have connectivity issues")
        print("\nCommon Solutions:")
        print("1. Ensure server and cameras are on the same network/subnet")
        print("2. Check firewall rules - allow RTSP (port 554) traffic")
        print("3. Verify network routing is configured correctly")
        print("4. Test camera connectivity from server using:")
        print("   - ping <camera_ip>")
        print("   - telnet <camera_ip> 554")
        print("   - ffprobe rtsp://user:pass@<camera_ip>:554/path")


async def main():
    """Main entry point."""
    if len(sys.argv) > 1:
        camera_id = sys.argv[1]
        await diagnose_camera(camera_id)
    else:
        await diagnose_all_cameras()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nDiagnostic interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
