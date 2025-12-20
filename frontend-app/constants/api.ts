/**
 * API Configuration
 * 
 * Set EXPO_PUBLIC_API_URL in your .env file or environment variables
 * For iOS Simulator: use http://localhost:8000
 * For physical device: use your computer's IP address (e.g., http://192.168.1.100:8000)
 */

const API_BASE_URL = process.env.EXPO_PUBLIC_API_URL || 'http://localhost:8000';

export const API_CONFIG = {
  BASE_URL: API_BASE_URL,
  WS_URL: API_BASE_URL.replace('http://', 'ws://').replace('https://', 'wss://'),
  ENDPOINTS: {
    HEALTH: `${API_BASE_URL}/health`,
    CONNECT: `${API_BASE_URL}/connect`,
    DISCONNECT: `${API_BASE_URL}/disconnect`,
    START_STREAMING: `${API_BASE_URL}/start-streaming`,
    STOP_STREAMING: `${API_BASE_URL}/stop-streaming`,
    START_RECORDING: `${API_BASE_URL}/start-recording`,
    STOP_RECORDING: `${API_BASE_URL}/stop-recording`,
    ANALYZE: `${API_BASE_URL}/analyze`,
    WS_DATA: `${API_BASE_URL.replace('http://', 'ws://').replace('https://', 'wss://')}/data`,
    // PAF Protocol endpoints
    PROTOCOL_SCAN_DEVICES: `${API_BASE_URL}/protocol/scan-devices`,
    PROTOCOL_INITIALIZE: `${API_BASE_URL}/protocol/initialize`,
    PROTOCOL_STATUS: `${API_BASE_URL}/protocol/status`,
    PROTOCOL_START_EEG: `${API_BASE_URL}/protocol/start-eeg`,
    PROTOCOL_START_IMPEDANCE_CHECK: `${API_BASE_URL}/protocol/start-impedance-check`,
    PROTOCOL_START: `${API_BASE_URL}/protocol/start`,
    PROTOCOL_COMPLETE_INTERVENTION: `${API_BASE_URL}/protocol/complete-intervention`,
    PROTOCOL_STOP: `${API_BASE_URL}/protocol/stop`,
    PROTOCOL_IMPEDANCE: `${API_BASE_URL}/protocol/impedance`,
    PROTOCOL_IMPEDANCE_STATS: `${API_BASE_URL}/protocol/impedance-stats`,
    PROTOCOL_DEVICE_INFO: `${API_BASE_URL}/protocol/device-info`,
    PROTOCOL_SAVE: `${API_BASE_URL}/protocol/save`,
    WS_PROTOCOL_EVENTS: `${API_BASE_URL.replace('http://', 'ws://').replace('https://', 'wss://')}/protocol/events`,
  },
} as const;

