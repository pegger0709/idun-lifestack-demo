from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import sys
import os

# Add parent directory to path to allow imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from live_eeg_plot import *
    from paf_protocol.paf_from_recording import compute_paf_from_recording
    from backend.paf_protocol_service import PAFProtocolService, ProtocolPhase
except ImportError:
    # Fallback for relative imports when run as module
    from ..live_eeg_plot import *
    from ..paf_protocol.paf_from_recording import compute_paf_from_recording
    from .paf_protocol_service import PAFProtocolService, ProtocolPhase
import random
import asyncio
import csv
import os
import signal
import atexit
from datetime import datetime

# Flag to signal shutdown to all handlers
_shutdown_flag = False
_cleanup_done = False

def cleanup_resources():
    """Synchronous cleanup of resources"""
    global protocol_service, streaming, _shutdown_flag, _cleanup_done
    
    if _cleanup_done:
        return
    _cleanup_done = True
    
    _shutdown_flag = True
    streaming = False
    
    print("[SHUTDOWN] Cleaning up resources...")
    
    if protocol_service:
        try:
            protocol_service.cleanup()
            print("[SHUTDOWN] Protocol service cleaned up")
        except Exception as e:
            print(f"[SHUTDOWN] Error cleaning up protocol service: {e}")
    
    print("[SHUTDOWN] Cleanup complete")

def signal_handler(signum, frame):
    """Handle SIGINT/SIGTERM for clean shutdown"""
    print(f"\n[SHUTDOWN] Received signal {signum}, shutting down...")
    cleanup_resources()
    # Force exit to avoid hanging
    os._exit(0)

# Register signal handlers for clean shutdown
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# Also register atexit as fallback
atexit.register(cleanup_resources)

# Create app without lifespan to avoid uvloop/uvicorn conflicts
app = FastAPI()

# Add CORS middleware to allow connections from any origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with specific origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Legacy streaming state
guardian_started = False
websocket_connected = False
use_guardian = False
active_connections = set()
should_stop = False
streaming = False
recording = False

# Protocol WebSocket connections (module-level for callback access)
protocol_websockets: set = set()
_main_event_loop = None  # Will be set when first WebSocket connects

def broadcast_phase_change(phase, message: str, remaining_sec):
    """Broadcast phase change to all connected protocol WebSocket clients.
    This is called from threading.Timer callbacks, so we need to properly
    schedule the async WebSocket sends on the main event loop.
    """
    global _main_event_loop
    
    if _shutdown_flag:
        return
    
    event = {
        "type": "phase_change",
        "phase": phase.value if hasattr(phase, 'value') else str(phase),
        "message": message,
        "remaining_seconds": remaining_sec
    }
    print(f"[WS] Broadcasting phase: {event['phase']}, remaining: {remaining_sec}, clients: {len(protocol_websockets)}")
    
    if not protocol_websockets:
        print("[WS] No WebSocket clients connected!")
        return
    
    if _main_event_loop is None:
        print("[WS] ERROR: No event loop available for WebSocket broadcast!")
        return
    
    async def send_to_all():
        disconnected = set()
        for ws in list(protocol_websockets):
            try:
                await ws.send_json(event)
                print(f"[WS] Sent to client successfully")
            except Exception as e:
                print(f"[WS] Error sending to client: {e}")
                disconnected.add(ws)
        
        # Remove disconnected clients
        for ws in disconnected:
            protocol_websockets.discard(ws)
    
    # Schedule the async function on the main event loop from this thread
    try:
        asyncio.run_coroutine_threadsafe(send_to_all(), _main_event_loop)
    except Exception as e:
        print(f"[WS] Error scheduling broadcast: {e}")
recording_file = None
csv_writer = None

# PAF Protocol Service (new integrated system)
protocol_service: Optional[PAFProtocolService] = None
protocol_websockets = set()  # WebSockets subscribed to protocol events

@app.get("/health")
def health():
    return {"status":"healthy"}

@app.get("/debug/connections")
def debug_connections():
    return {
        "active_connections": len(active_connections),
        "streaming": streaming,
        "recording": recording,
        "use_guardian": use_guardian
    }

@app.get("/connect")
def connect():
    global use_guardian
    try:
        token = os.getenv("IDUN_API_TOKEN")
        if not token:
            use_guardian = False
            return {"status":"error", "message":"IDUN_API_TOKEN not set, defaulting to dummy data", "error": "IDUN_API_TOKEN not set"}
        connection = start_async_stream_in_background()
        use_guardian = True
        print("Connection success:", connection)
        return {"status":"connected", "message":"connection success"}
    except Exception as e:
        print("Error connecting:", e)
        use_guardian = False
        return {"status":"error", "message":"connection failed, defaulting to dummy data", "error": str(e)}

@app.get("/start-streaming")
def stream():
    global streaming
    if not streaming:
        streaming = True
    return {"status":"streaming", "message":"stream started"}

@app.get("/stop-streaming")
def stop_stream():
    global streaming, recording
    if streaming:
        streaming = False
    if recording:
        recording = False
    return {"status":"paused", "message":"stream paused"}

@app.get("/start-recording")
def start_recording():
    global recording, recording_file, csv_writer
    if not recording:
        recording = True
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # Ensure recordings directory exists
        recordings_dir = "recordings"
        os.makedirs(recordings_dir, exist_ok=True)
        filename = os.path.join(recordings_dir, f"eeg_recording_{timestamp}.csv")
        recording_file = open(filename, "w", newline='')
        csv_writer = csv.writer(recording_file)
        csv_writer.writerow(['timestamp','ch1'])
        return {"status": "recording started", "message":"started recording data"}
    return {"status": "already recording", "message":"recording already in progress"}


@app.get("/stop-recording")
def stop_recording():
    global recording, recording_file, csv_writer
    if recording:
        recording = False
        filename = recording_file.name
        if recording_file:
            recording_file.close()
        recording_file = None
        csv_writer = None
        return {"status":"recording_stopped", "message": f"recording saved to {filename}", "filename": filename}
    return {"status": "not_recording", "message":"no recording in progress"} 

class AnalyzeRequest(BaseModel):
    filename: str

@app.post("/analyze")
def analyze(request: AnalyzeRequest):
    filename = request.filename
    if not filename:
        return {"status":"error", "message":"filename is required", "readiness": None}
    
    try:
        # compute_paf_from_recording expects CSV path, auto-finds JSON metadata
        # If filename doesn't have .csv extension, try adding it
        if not filename.endswith('.csv'):
            # Try with .csv extension
            csv_path = filename if filename.endswith('.csv') else filename + '.csv'
        else:
            csv_path = filename
        
        if not os.path.exists(csv_path):
            return {
                "status": "error",
                "message": f"File not found: {csv_path}",
                "readiness": None
            }
        
        result = compute_paf_from_recording(csv_path)
        return {"status":"analysis_complete", "message": "analysis complete", "readiness": result}
    except Exception as e:
        print(f"Error analyzing {filename}: {e}")
        import traceback
        traceback.print_exc()
        return {
            "status": "error",
            "message": f"Analysis failed: {str(e)}",
            "readiness": None
        }

@app.websocket("/data")
async def data_endpoint(websocket: WebSocket):
    global active_connections, _shutdown_flag
    try:
        await websocket.accept()
        print(f"WebSocket connection accepted from {websocket.client}")
        active_connections.add(websocket)
        print(f"Active connections: {len(active_connections)}")
    except Exception as e:
        print(f"Error accepting WebSocket connection: {e}")
        return
    
    last_sent_index = 0
    try:
        while not _shutdown_flag:
            try:
                if streaming:
                    if use_guardian:
                        with buf_lock:
                            current_data = list(buf)
                        
                        if len(current_data) > last_sent_index:
                            interval = len(current_data) - last_sent_index
                            data = current_data[-interval:]
                            ts = [d[0] for d in data]
                            vals = [d[1] for d in data]
                            
                            if recording and csv_writer:
                                for timestamp, value in zip(ts, vals):
                                    csv_writer.writerow([timestamp, value])
                                recording_file.flush()
                            payload = {
                                    "timestamps":ts,
                                    "data":vals,
                            }
                            
                            await websocket.send_json(payload)
                            await asyncio.sleep(.01)
                            last_sent_index = len(current_data)
                        else:
                            await asyncio.sleep(.05)
                    else:
                        import numpy as np
                        t = last_sent_index / 250.0  # Convert to seconds
                        base_signal = 10.0 * np.sin(2 * np.pi * 10.0 * t)  # 10 Hz alpha
                        noise = random.uniform(-2, 2)
                        vals = [base_signal + noise]
                        ts = [last_sent_index]

                        if recording and csv_writer:
                            for timestamp, value in zip(ts, vals):
                                csv_writer.writerow([timestamp, value])
                            recording_file.flush()
                        
                        payload = {
                                "timestamps":ts,
                                "data":vals,
                        }

                        await websocket.send_json(payload)
                        await asyncio.sleep(.01)
                        last_sent_index += 1
                else:
                    await asyncio.sleep(.05)
            except asyncio.CancelledError:
                # Shutdown signal received
                break
            except WebSocketDisconnect:
                break
            except Exception as e:
                if not _shutdown_flag:
                    print("Error sending eeg data:", e)
                break
    except asyncio.CancelledError:
        pass  # Normal shutdown
    except WebSocketDisconnect:
        pass  # Normal disconnect
    except Exception as e:
        if not _shutdown_flag:
            print("WebSocket error:", e)
    finally:
        print("WebSocket closed")
        active_connections.discard(websocket)
        # Don't try to close - just let it be cleaned up

@app.get("/disconnect")
async def disconnect():
    global streaming, protocol_service
    streaming = False
    
    # Clean up protocol service
    if protocol_service:
        protocol_service.cleanup()
        protocol_service = None
    
    # Just clear the sets - don't try to close individual WebSockets
    active_connections.clear()
    protocol_websockets.clear()

    return {"status": "disconnected", "message": "all websockets disconnected"}


_scanning = False

@app.get("/protocol/scan-devices")
async def scan_for_devices():
    """Scan for available IDUN Guardian devices using IDUN SDK"""
    global _scanning
    
    # Prevent multiple simultaneous scans
    if _scanning:
        print("[WARN] Scan already in progress, ignoring duplicate request")
        return {
            "status": "pending",
            "message": "Device scan already in progress"
        }
    
    _scanning = True
    try:
        # First try using IDUN SDK's built-in search (this was working before)
        try:
            from idun_guardian_sdk import GuardianClient
            
            print("[INFO] Scanning using IDUN SDK...")
            token = os.getenv("IDUN_API_TOKEN")
            
            if token:
                client = GuardianClient(api_token=token, debug=False)
            else:
                client = GuardianClient()
            
            # SDK's search_device finds and auto-selects a device
            await client.search_device()
            
            # Get the found device's MAC address for display
            try:
                mac_address = await client.get_device_mac_address()
                print(f"[INFO] IDUN SDK found device MAC: {mac_address}")
                
                # Disconnect so user can choose to connect later
                try:
                    await client.disconnect_device()
                except:
                    pass
                
                # Return "auto" as address - the SDK will re-search when connecting
                # On macOS, BLE uses UUIDs not MAC addresses, so we can't use MAC to connect
                return {
                    "status": "success",
                    "devices": [{
                        "address": "auto",  # Use auto-discovery for connection
                        "name": f"IDUN Guardian ({mac_address[-5:] if mac_address else 'Unknown'})",
                        "mac_address": mac_address,  # Include MAC for display
                        "rssi": None
                    }],
                    "count": 1
                }
            except Exception as e:
                print(f"[INFO] Could not get device MAC after search: {e}")
                # Device was found but couldn't get MAC - still return success
                return {
                    "status": "success",
                    "devices": [{
                        "address": "auto",
                        "name": "IDUN Guardian (Auto-detected)",
                        "rssi": None
                    }],
                    "count": 1
                }
                
        except Exception as sdk_error:
            print(f"[INFO] IDUN SDK search failed: {sdk_error}, falling back to BLE scan...")
        
        # Fallback to BLE scan with bleak
        from bleak import BleakScanner
        
        print("[INFO] Scanning for BLE devices with bleak...")
        devices = await BleakScanner.discover(timeout=5.0)
        
        print(f"[INFO] Found {len(devices)} total BLE devices")
        
        guardian_devices = []
        other_devices = []
        
        for device in devices:
            name = device.name or ""
            address = device.address or ""
            rssi = device.rssi if hasattr(device, 'rssi') else None
            
            # Log all devices for debugging
            print(f"  - {name or 'Unknown'} ({address}) RSSI: {rssi}")
            
            device_info = {
                "address": address,
                "name": name or f"Unknown ({address[-8:] if address else 'N/A'})",
                "rssi": rssi
            }
            
            # Check if this looks like a Guardian device
            # Known patterns: IGE-XXXXXX, IGEB-XXXXX, IG-XXXXX
            name_upper = name.upper()
            is_guardian = (
                "IDUN" in name_upper or 
                "GUARDIAN" in name_upper or 
                name_upper.startswith("IGE-") or  # e.g., IGE-EDF494
                name_upper.startswith("IGEB-") or
                name_upper.startswith("IG-") or
                address.upper().startswith("C7-01") or
                address.upper().startswith("F3-27") or
                "EEG" in name_upper
            )
            
            if is_guardian:
                guardian_devices.append(device_info)
            elif name:
                other_devices.append(device_info)
        
        # Sort by signal strength
        guardian_devices.sort(key=lambda x: x.get('rssi') or -100, reverse=True)
        other_devices.sort(key=lambda x: x.get('rssi') or -100, reverse=True)
        
        if guardian_devices:
            return {
                "status": "success",
                "devices": guardian_devices,
                "count": len(guardian_devices)
            }
        elif other_devices:
            return {
                "status": "success",
                "devices": other_devices[:10],
                "count": len(other_devices),
                "message": "No confirmed Guardian devices. Showing nearby devices."
            }
        else:
            return {
                "status": "success",
                "devices": [],
                "count": 0,
                "message": "No BLE devices found. Make sure Bluetooth is on and device is powered."
            }
            
    except ImportError as e:
        return {
            "status": "error",
            "message": f"Required library not installed: {e}",
            "devices": []
        }
    except Exception as e:
        print(f"[ERROR] Device scan failed: {e}")
        import traceback
        traceback.print_exc()
        return {
            "status": "error",
            "message": f"Scan failed: {str(e)}",
            "devices": []
        }
    finally:
        _scanning = False

_initializing = False

@app.get("/protocol/initialize")
def initialize_protocol(device_address: Optional[str] = None):
    """Initialize the PAF protocol service with optional device address"""
    global protocol_service, _initializing
    
    # Prevent multiple simultaneous initialization attempts
    if _initializing:
        print("[WARN] Initialization already in progress, ignoring duplicate request")
        return {
            "status": "pending",
            "message": "Initialization already in progress"
        }
    
    try:
        _initializing = True
        
        # Clean up existing protocol service if any
        if protocol_service:
            print("[INFO] Cleaning up existing protocol service before re-initializing...")
            try:
                protocol_service.cleanup()
            except Exception as e:
                print(f"[WARN] Error cleaning up old protocol service: {e}")
            protocol_service = None
        
        token = os.getenv("IDUN_API_TOKEN")
        protocol_service = PAFProtocolService(out_dir="recordings")
        
        # Register the phase change callback so WebSocket clients get updates
        protocol_service.add_phase_callback(broadcast_phase_change)
        print("[INFO] Registered phase change callback for WebSocket broadcasts")
        
        # If address is "auto" or empty, let SDK auto-discover
        actual_address = None if (device_address is None or device_address == "auto") else device_address
        
        if not protocol_service.initialize_guardian(token, device_address=actual_address):
            _initializing = False
            return {
                "status": "error",
                "message": "Failed to initialize Guardian. Check IDUN_API_TOKEN."
            }
        
        _initializing = False
        return {
            "status": "success",
            "message": "Protocol service initialized",
            "status_info": protocol_service.get_status()
        }
    except Exception as e:
        _initializing = False
        return {
            "status": "error",
            "message": f"Initialization failed: {str(e)}"
        }

@app.get("/protocol/status")
def get_protocol_status():
    """Get current protocol status"""
    global protocol_service
    if not protocol_service:
        return {
            "status": "error",
            "message": "Protocol service not initialized. Call /protocol/initialize first."
        }
    
    return {
        "status": "success",
        **protocol_service.get_status()
    }

@app.get("/protocol/start-eeg")
def start_eeg_stream():
    """Start the EEG stream (required before starting protocol)"""
    global protocol_service
    if not protocol_service:
        return {
            "status": "error",
            "message": "Protocol service not initialized"
        }
    
    if protocol_service.start_eeg_stream():
        return {
            "status": "success",
            "message": "EEG stream started. Wait for stream to be ready."
        }
    return {
        "status": "error",
        "message": "Failed to start EEG stream"
    }

@app.get("/protocol/start-impedance-check")
def start_impedance_check():
    """Start impedance check (10 seconds)"""
    global protocol_service
    if not protocol_service:
        return {"status": "error", "message": "Protocol service not initialized"}
    
    if protocol_service.start_impedance_check():
        return {
            "status": "success",
            "message": "Impedance check started",
            "status_info": protocol_service.get_status()
        }
    return {
        "status": "error",
        "message": "Failed to start impedance check",
        "status_info": protocol_service.get_status()
    }

@app.get("/protocol/start")
def start_paf_protocol(intervention_label: Optional[str] = None):
    """Start the PAF protocol with optional intervention label"""
    global protocol_service
    if not protocol_service:
        return {
            "status": "error",
            "message": "Protocol service not initialized"
        }
    
    if protocol_service.start_paf_protocol(intervention_label):
        return {
            "status": "success",
            "message": "PAF protocol started",
            "status_info": protocol_service.get_status()
        }
    return {
        "status": "error",
        "message": "Failed to start protocol. Ensure EEG stream is ready.",
        "status_info": protocol_service.get_status()
    }

@app.get("/protocol/complete-intervention")
def complete_intervention():
    """Mark intervention as complete and start post-intervention measurement"""
    global protocol_service
    if not protocol_service:
        return {"status": "error", "message": "Protocol service not initialized"}
    
    if protocol_service.start_intervention():
        return {
            "status": "success",
            "message": "Starting post-intervention measurement",
            "status_info": protocol_service.get_status()
        }
    return {
        "status": "error",
        "message": "Cannot start intervention. Baseline must be complete first.",
        "status_info": protocol_service.get_status()
    }

@app.get("/protocol/stop")
def stop_paf_protocol():
    """Stop/abort the PAF protocol"""
    global protocol_service
    if not protocol_service:
        return {"status": "error", "message": "Protocol service not initialized"}
    
    protocol_service.stop_protocol()
    return {
        "status": "success",
        "message": "Protocol stopped",
        "status_info": protocol_service.get_status()
    }

@app.get("/protocol/save")
def save_protocol_recording(intervention_label: Optional[str] = None):
    """Save the current recording with metadata and analyze pre/post intervention"""
    global protocol_service
    if not protocol_service:
        return {"status": "error", "message": "Protocol service not initialized"}
    
    try:
        files = protocol_service.save_recording(intervention_label)
        
        # Auto-analyze the recording
        # compute_paf_from_recording now handles both old and new protocol automatically:
        # - New protocol: returns pre_iaf_hz, post_iaf_hz, delta_iaf_hz
        # - Old protocol: returns readiness_iaf_hz
        try:
            result = compute_paf_from_recording(files["csv_path"])
            
            # Build intervention analysis from result
            analysis_result = {
                "intervention_label": intervention_label or protocol_service.intervention_label,
            }
            
            if result.get("protocol_type") == "new_eyes_closed":
                # New protocol: pre and post eyes-closed
                analysis_result["baseline"] = {
                    "readiness_iaf_hz": result.get("pre_iaf_hz"),
                    "readiness_source": result.get("pre_iaf_source"),
                    "eyes_closed": result.get("eyes_closed_pre", {})
                }
                analysis_result["post_intervention"] = {
                    "readiness_iaf_hz": result.get("post_iaf_hz"),
                    "readiness_source": result.get("post_iaf_source"),
                    "eyes_closed": result.get("eyes_closed_post", {})
                }
                
                pre_iaf = result.get("pre_iaf_hz")
                post_iaf = result.get("post_iaf_hz")
                delta = result.get("delta_iaf_hz")
                
                if pre_iaf is not None and post_iaf is not None and delta is not None:
                    analysis_result["effect"] = {
                        "delta_hz": delta,
                        "percent_change": (delta / pre_iaf * 100) if pre_iaf != 0 else 0
                    }
                else:
                    analysis_result["effect"] = None
            else:
                # Old protocol: only baseline
                analysis_result["baseline"] = {
                    "readiness_iaf_hz": result.get("readiness_iaf_hz"),
                    "readiness_source": result.get("readiness_source"),
                    "eyes_closed": result.get("eyes_closed", {})
                }
                analysis_result["post_intervention"] = None
                analysis_result["effect"] = None
            
            return {
                "status": "success",
                "message": "Recording saved and analyzed",
                "files": files,
                "analysis": result,
                "intervention_analysis": analysis_result
            }
        except Exception as e:
            print(f"Auto-analysis failed: {e}")
            import traceback
            traceback.print_exc()
            return {
                "status": "partial_success",
                "message": f"Recording saved but analysis failed: {str(e)}",
                "files": files,
                "analysis": None
            }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {
            "status": "error",
            "message": f"Failed to save recording: {str(e)}"
        }

@app.get("/protocol/impedance")
def get_impedance():
    """Get current impedance reading"""
    global protocol_service
    if not protocol_service:
        return {"status": "error", "message": "Protocol service not initialized"}
    
    imp = protocol_service.get_impedance()
    if imp is None:
        return {
            "status": "no_data",
            "impedance": None,
            "message": "No impedance reading available yet"
        }
    
    return {
        "status": "success",
        "impedance_kohm": imp / 1000.0,
        "impedance_ohm": imp,
        "quality": "good" if imp < 300000 else "poor"
    }

@app.get("/protocol/impedance-stats")
def get_impedance_stats():
    """Get impedance statistics from the check period"""
    global protocol_service
    if not protocol_service:
        return {"status": "error", "message": "Protocol service not initialized"}
    
    stats = protocol_service.get_impedance_stats(verbose=True)
    return {
        "status": "success",
        **stats
    }

@app.get("/protocol/device-info")
def get_device_info():
    """Get connected device information (MAC address, name, etc.)"""
    global protocol_service
    if not protocol_service:
        return {"status": "error", "message": "Protocol service not initialized"}
    
    try:
        device_info = protocol_service.get_device_info()
        return {
            "status": "success",
            **device_info
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to get device info: {str(e)}",
            "mac_address": None,
            "name": None
        }

@app.websocket("/protocol/events")
async def protocol_events_websocket(websocket: WebSocket):
    """WebSocket endpoint for protocol phase change events"""
    global protocol_service, _shutdown_flag, _main_event_loop
    
    # Capture the event loop for use in thread callbacks
    _main_event_loop = asyncio.get_running_loop()
    
    try:
        await websocket.accept()
    except Exception as e:
        print(f"Error accepting protocol WebSocket: {e}")
        return
    
    # Add to module-level set so broadcast_phase_change can send to this client
    protocol_websockets.add(websocket)
    print(f"[WS] Protocol WebSocket connected. Total clients: {len(protocol_websockets)}, event loop captured: {_main_event_loop is not None}")
    
    try:
        while not _shutdown_flag:
            try:
                # Use wait_for with timeout to check shutdown flag periodically
                data = await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
                await websocket.send_json({"type": "ack", "message": "received"})
            except asyncio.TimeoutError:
                # Normal timeout, check shutdown flag and continue
                continue
            except asyncio.CancelledError:
                break
            except WebSocketDisconnect:
                break
            except Exception:
                if not _shutdown_flag:
                    break
    except asyncio.CancelledError:
        pass  # Normal shutdown
    except Exception:
        pass  # Suppress all errors during shutdown
    finally:
        protocol_websockets.discard(websocket)
        print("Protocol WebSocket closed")
