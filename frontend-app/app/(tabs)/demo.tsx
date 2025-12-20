import React, { useState, useEffect, useRef } from 'react';
import { StyleSheet, View, Text as NativeText, TouchableOpacity, Modal, TextInput, ScrollView, ActivityIndicator } from 'react-native';
import { API_CONFIG } from '@/constants/api';

// Types
type AppPhase = 'reminder' | 'connection' | 'impedance' | 'recording' | 'result';
type ConnectionStep = 'discover' | 'selecting' | 'connecting' | 'connected';
type ImpedanceStep = 'ready' | 'checking' | 'complete';
type RecordingStep = 'setup' | 'instructions_pre' | 'eyes_closed_pre' | 'intervention' | 'instructions_post' | 'eyes_closed_post' | 'complete';

interface Device {
	address: string;
	name: string;
	rssi: number | null;
}

interface ImpedanceStats {
	mean_kohm: number;
	quality: 'good' | 'poor' | 'unknown';
	count: number;
}

export default function DemoScreen() {
	// App state
	const [currentPhase, setCurrentPhase] = useState<AppPhase>('reminder');
	const [showInfo, setShowInfo] = useState(false);
	
	// Connection state
	const [connectionStep, setConnectionStep] = useState<ConnectionStep>('discover');
	const [devices, setDevices] = useState<Device[]>([]);
	const [selectedDevice, setSelectedDevice] = useState<Device | null>(null);
	const [isScanning, setIsScanning] = useState(false);
	const [isConnecting, setIsConnecting] = useState(false);
	const [connectionError, setConnectionError] = useState<string | null>(null);
	
	// Impedance state
	const [impedanceStep, setImpedanceStep] = useState<ImpedanceStep>('ready');
	const [impedanceCountdown, setImpedanceCountdown] = useState(10);
	const [currentImpedance, setCurrentImpedance] = useState<number | null>(null);
	const [impedanceStats, setImpedanceStats] = useState<ImpedanceStats | null>(null);
	
	// Recording state
	const [recordingStep, setRecordingStep] = useState<RecordingStep>('setup');
	const [intervention, setIntervention] = useState('');
	const [countdown, setCountdown] = useState(5);
	const [recordingTime, setRecordingTime] = useState(60);
	const [protocolMessage, setProtocolMessage] = useState('');
	
	// Result state
	const [hasIntervention, setHasIntervention] = useState(false);
	const [preScore, setPreScore] = useState<number | null>(null);
	const [postScore, setPostScore] = useState<number | null>(null);
	const [readinessScore, setReadinessScore] = useState<number | null>(null);
	const [hasError, setHasError] = useState(false);
	const [errorMessage, setErrorMessage] = useState('');
	
	// Refs
	const protocolWsRef = useRef<WebSocket | null>(null);
	const impedanceIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
	const countdownIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
	
	// Constants
	const IMPEDANCE_THRESHOLD = 300; // kΩ - threshold for good quality
	
	// Protocol WebSocket connection
	useEffect(() => {
		const connectProtocolWebSocket = () => {
			const ws = new WebSocket(API_CONFIG.ENDPOINTS.WS_PROTOCOL_EVENTS);
			
			ws.onopen = () => {
				console.log("Protocol WebSocket connected");
			};
			
			ws.onmessage = (event) => {
				try {
					const data = JSON.parse(event.data);
					console.log("Protocol event:", data.phase, "remaining:", data.remaining_seconds, "msg:", data.message);
					
					if (data.type === 'phase_change') {
						setProtocolMessage(data.message);
						
						// Handle phase transitions (new protocol flow)
						if (data.phase === 'instructions_pre') {
							console.log(">>> PHASE: instructions_pre");
							setRecordingStep('instructions_pre');
							setCountdown(data.remaining_seconds || 5);
						} else if (data.phase === 'eyes_closed_pre') {
							console.log(">>> PHASE: eyes_closed_pre");
							setRecordingStep('eyes_closed_pre');
							setRecordingTime(data.remaining_seconds || 30);
						} else if (data.phase === 'intervention') {
							console.log(">>> PHASE: intervention");
							setRecordingStep('intervention');
							setRecordingTime(data.remaining_seconds || 60);
						} else if (data.phase === 'instructions_post') {
							console.log(">>> PHASE: instructions_post");
							setRecordingStep('instructions_post');
							setCountdown(data.remaining_seconds || 5);
						} else if (data.phase === 'eyes_closed_post') {
							console.log(">>> PHASE: eyes_closed_post");
							setRecordingStep('eyes_closed_post');
							setRecordingTime(data.remaining_seconds || 30);
						} else if (data.phase === 'complete') {
							console.log(">>> PHASE: complete");
							setRecordingStep('complete');
							// Auto-save and move to results
							saveAndAnalyze();
						} else if (data.phase === 'idle' && data.message.includes('Impedance check complete')) {
							// Impedance check finished
							setImpedanceStep('complete');
							fetchImpedanceStats();
						} else {
							console.log(">>> UNHANDLED PHASE:", data.phase);
						}
					}
				} catch (error) {
					console.error("Error parsing protocol event:", error);
				}
			};
			
			ws.onerror = (error) => {
				console.error("Protocol WebSocket error:", error);
			};
			
			ws.onclose = (event) => {
				console.log("Protocol WebSocket disconnected");
				if (event.code !== 1000) {
					setTimeout(connectProtocolWebSocket, 3000);
				}
			};
			
			protocolWsRef.current = ws;
		};
		
		connectProtocolWebSocket();
		
		return () => {
			if (protocolWsRef.current) {
				protocolWsRef.current.close();
			}
			if (impedanceIntervalRef.current) {
				clearInterval(impedanceIntervalRef.current);
			}
			if (countdownIntervalRef.current) {
				clearInterval(countdownIntervalRef.current);
			}
		};
	}, []);
	
	// Countdown timer effect for instruction phases and recording phases
	useEffect(() => {
		console.log("Countdown effect triggered, recordingStep:", recordingStep);
		
		// Handle 5-second instruction countdowns
		if (recordingStep === 'instructions_pre' || recordingStep === 'instructions_post') {
			console.log("Starting instruction countdown timer");
			const interval = setInterval(() => {
				setCountdown(prev => {
					const next = prev <= 1 ? 0 : prev - 1;
					console.log("Countdown tick:", next);
					return next;
				});
			}, 1000);
			return () => {
				console.log("Clearing instruction countdown interval");
				clearInterval(interval);
			};
		}
		
		// Handle recording time countdowns (eyes closed and intervention phases)
		if (recordingStep === 'eyes_closed_pre' || recordingStep === 'intervention' || recordingStep === 'eyes_closed_post') {
			console.log("Starting recording countdown timer for:", recordingStep);
			const interval = setInterval(() => {
				setRecordingTime(prev => {
					const next = prev <= 1 ? 0 : prev - 1;
					if (prev % 10 === 0 || prev <= 5) {
						console.log("Recording time tick:", next);
					}
					return next;
				});
			}, 1000);
			return () => {
				console.log("Clearing recording countdown interval");
				clearInterval(interval);
			};
		}
	}, [recordingStep]);
 
	// ============= CONNECTION FUNCTIONS =============
 
	async function scanForDevices() {
		setIsScanning(true);
		setConnectionError(null);
		setDevices([]);
		
		try {
			const response = await fetch(API_CONFIG.ENDPOINTS.PROTOCOL_SCAN_DEVICES);
			const data = await response.json();
			
			if (data.status === 'success') {
				setDevices(data.devices);
				if (data.devices.length > 0) {
					setConnectionStep('selecting');
				} else {
					setConnectionError('No Guardian devices found. Make sure your device is powered on and nearby.');
				}
			} else {
				setConnectionError(data.message || 'Failed to scan for devices');
			}
		} catch (error) {
			console.error("Scan error:", error);
			setConnectionError('Failed to scan for devices. Check your connection.');
		} finally {
			setIsScanning(false);
		}
	}
	
	async function connectToDevice() {
		if (!selectedDevice) return;
  
		setIsConnecting(true);
		setConnectionStep('connecting');
		setConnectionError(null);
		
		try {
			// Initialize protocol service with selected device
			// This connects to device and starts impedance streaming (but NOT EEG yet)
			const initUrl = `${API_CONFIG.ENDPOINTS.PROTOCOL_INITIALIZE}?device_address=${encodeURIComponent(selectedDevice.address)}`;
			const initResponse = await fetch(initUrl);
			const initData = await initResponse.json();
			
			if (initData.status === 'error') {
				throw new Error(initData.message);
			}
			
			if (initData.status === 'pending') {
				// Initialization already in progress, wait and retry
				console.log("Initialization pending, waiting...");
				await new Promise(resolve => setTimeout(resolve, 2000));
				return connectToDevice(); // Retry
			}
			
			// Wait for device to be connected (impedance streaming will be active)
			let attempts = 0;
			const maxAttempts = 30;
			
			const checkConnected = async (): Promise<boolean> => {
				attempts++;
				const statusResponse = await fetch(API_CONFIG.ENDPOINTS.PROTOCOL_STATUS);
				const statusData = await statusResponse.json();
				
				// Check if we have impedance data (indicates device is streaming)
				if (statusData.impedance !== null) {
					console.log("Device connected, impedance streaming active");
					return true;
				}
				
				if (attempts < maxAttempts) {
					await new Promise(resolve => setTimeout(resolve, 1000));
					return checkConnected();
				}
				
				// Even if no impedance yet, proceed after timeout
				console.log("Connection timeout, proceeding anyway");
				return true;
			};
			
			await checkConnected();
			
			setConnectionStep('connected');
			
		} catch (error: any) {
			console.error("Connection error:", error);
			setConnectionError(error.message || 'Failed to connect to device');
			setConnectionStep('selecting');
		} finally {
			setIsConnecting(false);
		}
	}
	
	// ============= IMPEDANCE FUNCTIONS =============
	
	async function startImpedanceCheck() {
		setImpedanceStep('checking');
		setImpedanceCountdown(10);
		setImpedanceStats(null);
		
		try {
			const response = await fetch(API_CONFIG.ENDPOINTS.PROTOCOL_START_IMPEDANCE_CHECK);
			const data = await response.json();
			
			if (data.status !== 'success') {
				throw new Error(data.message);
			}
			
			// Start countdown
			let remaining = 10;
			countdownIntervalRef.current = setInterval(() => {
				remaining--;
				setImpedanceCountdown(remaining);
				
				if (remaining <= 0) {
					if (countdownIntervalRef.current) {
						clearInterval(countdownIntervalRef.current);
					}
				}
			}, 1000);
			
			// Poll impedance during check
			impedanceIntervalRef.current = setInterval(async () => {
				try {
					const impResponse = await fetch(API_CONFIG.ENDPOINTS.PROTOCOL_IMPEDANCE);
					const impData = await impResponse.json();
					if (impData.status === 'success') {
						setCurrentImpedance(impData.impedance_kohm);
					}
				} catch (e) {
					// Ignore polling errors
				}
			}, 500);
			
			// Stop polling after 12 seconds (buffer for completion)
			setTimeout(() => {
				if (impedanceIntervalRef.current) {
					clearInterval(impedanceIntervalRef.current);
				}
				fetchImpedanceStats();
			}, 12000);
			
		} catch (error: any) {
			console.error("Impedance check error:", error);
			setImpedanceStep('ready');
		}
	}
	
	async function fetchImpedanceStats() {
		try {
			console.log("[IMPEDANCE] Fetching impedance stats...");
			const response = await fetch(API_CONFIG.ENDPOINTS.PROTOCOL_IMPEDANCE_STATS);
			const data = await response.json();
			
			console.log("[IMPEDANCE] Received stats:", JSON.stringify(data, null, 2));
			
			if (data.status === 'success' && data.count > 0 && data.mean_kohm !== null && data.mean_kohm !== undefined) {
				console.log(`[IMPEDANCE] Setting stats: mean=${data.mean_kohm} kΩ, quality=${data.quality}, count=${data.count}`);
				setImpedanceStats({
					mean_kohm: data.mean_kohm,
					quality: data.quality,
					count: data.count
				});
			} else {
				console.log(`[IMPEDANCE] No valid stats: status=${data.status}, count=${data.count}, mean_kohm=${data.mean_kohm}`);
				setImpedanceStats({
					mean_kohm: 0,
					quality: 'unknown',
					count: data.count || 0
				});
			}
			setImpedanceStep('complete');
		} catch (error) {
			console.error("[IMPEDANCE] Error fetching impedance stats:", error);
			setImpedanceStep('complete');
		}
	}
	
	// ============= RECORDING FUNCTIONS =============
	
	const [isStartingEEG, setIsStartingEEG] = useState(false);
	
	async function proceedToRecording() {
		// Start EEG stream before moving to recording phase
		setIsStartingEEG(true);
		
		try {
			console.log("Starting EEG stream...");
			const eegResponse = await fetch(API_CONFIG.ENDPOINTS.PROTOCOL_START_EEG);
			const eegData = await eegResponse.json();
			
			if (eegData.status === 'error') {
				throw new Error(eegData.message);
			}
			
			// Wait for EEG to be ready
			let attempts = 0;
			const maxAttempts = 30;
			
			const checkReady = async (): Promise<boolean> => {
				attempts++;
				const statusResponse = await fetch(API_CONFIG.ENDPOINTS.PROTOCOL_STATUS);
				const statusData = await statusResponse.json();
				
				if (statusData.eeg_ready) {
					console.log("EEG stream ready!");
					return true;
				}
				
				if (statusData.eeg_error) {
					throw new Error(statusData.eeg_error);
				}
				
				if (attempts < maxAttempts) {
					await new Promise(resolve => setTimeout(resolve, 1000));
					return checkReady();
				}
				
				throw new Error('EEG stream did not become ready in time');
			};
			
			await checkReady();
			
			setCurrentPhase('recording');
		} catch (error: any) {
			console.error("Error starting EEG:", error);
			setConnectionError(error.message || 'Failed to start EEG');
		} finally {
			setIsStartingEEG(false);
		}
	}
	
	async function startProtocol() {
		setHasIntervention(intervention.trim() !== '');
		setRecordingStep('instructions_pre');
		
		try {
			const url = intervention 
				? `${API_CONFIG.ENDPOINTS.PROTOCOL_START}?intervention_label=${encodeURIComponent(intervention)}`
				: API_CONFIG.ENDPOINTS.PROTOCOL_START;
			
			const response = await fetch(url);
			const data = await response.json();
			
			if (data.status === 'error') {
				throw new Error(data.message);
			}
			
			// Protocol will progress automatically via WebSocket events
		} catch (error: any) {
			console.error("Protocol start error:", error);
			setHasError(true);
			setErrorMessage(error.message || 'Failed to start protocol');
			setCurrentPhase('result');
		}
	}
	
	async function completeIntervention() {
		try {
			const response = await fetch(API_CONFIG.ENDPOINTS.PROTOCOL_COMPLETE_INTERVENTION);
			const data = await response.json();
			
			if (data.status === 'error') {
				throw new Error(data.message);
			}
			
			setRecordingStep('instructions_post');
		} catch (error: any) {
			console.error("Error completing intervention:", error);
		}
	}
	
	async function saveAndAnalyze() {
		try {
			const url = intervention 
				? `${API_CONFIG.ENDPOINTS.PROTOCOL_SAVE}?intervention_label=${encodeURIComponent(intervention)}`
				: API_CONFIG.ENDPOINTS.PROTOCOL_SAVE;
			
			const response = await fetch(url);
			const data = await response.json();
			
			console.log("Save and analyze response:", data);
			
			if (data.status === 'success' && data.analysis) {
				// New protocol: pre_iaf_hz and post_iaf_hz in analysis result
				if (data.analysis.protocol_type === 'new_eyes_closed') {
					const preIaf = data.analysis.pre_iaf_hz;
					const postIaf = data.analysis.post_iaf_hz;
					const deltaIaf = data.analysis.delta_iaf_hz;
					
					console.log("New protocol results:", { preIaf, postIaf, deltaIaf });
					
					setPreScore(preIaf || 0);
					setPostScore(postIaf || 0);
					setReadinessScore(postIaf || 0);
				}
				// Old protocol fallback
				else if (data.intervention_analysis && data.intervention_analysis.baseline && data.intervention_analysis.post_intervention) {
					setPreScore(data.intervention_analysis.baseline.readiness_iaf_hz || 0);
					setPostScore(data.intervention_analysis.post_intervention.readiness_iaf_hz || 0);
				} else {
					const iaf = data.analysis.readiness_iaf_hz;
					setReadinessScore(iaf || 0);
				}
			} else if (data.status === 'partial_success') {
				// Recording saved but analysis failed
				setHasError(true);
				setErrorMessage(data.message || 'Recording saved but analysis failed');
			} else {
				setHasError(true);
				setErrorMessage(data.message || 'Analysis failed');
			}
		} catch (error: any) {
			console.error("Save and analyze error:", error);
			setHasError(true);
			setErrorMessage(error.message || 'Failed to save and analyze');
		}
		
		setCurrentPhase('result');
	}
	
	function resetApp() {
		setCurrentPhase('reminder');
		setConnectionStep('discover');
		setDevices([]);
		setSelectedDevice(null);
		setImpedanceStep('ready');
		setImpedanceStats(null);
		setRecordingStep('setup');
		setIntervention('');
		setHasIntervention(false);
		setPreScore(null);
		setPostScore(null);
		setReadinessScore(null);
		setHasError(false);
		setErrorMessage('');
	}
	
	// ============= RENDER FUNCTIONS =============
	
	const renderReminderPage = () => (
		<View style={styles.card}>
			<View style={styles.iconContainer}>
				<View style={[styles.iconCircle, { backgroundColor: '#3B82F6' }]}>
					<NativeText style={styles.iconText}>📅</NativeText>
				</View>
			</View>
	  
			<View style={styles.notificationCard}>
				<View style={styles.notificationHeader}>
					<View style={{ flex: 1 }}>
						<NativeText style={styles.notificationTime}>Today at 2:00 PM</NativeText>
						<NativeText style={styles.notificationTitle}>Deep Work Session</NativeText>
						<NativeText style={styles.notificationDesc}>
							Time to prepare for your focused work session. Check your readiness level before starting.
						</NativeText>
					</View>
					<TouchableOpacity 
						style={styles.infoButton}
						onPress={() => setShowInfo(true)}
					>
						<NativeText style={styles.infoButtonText}>?</NativeText>
					</TouchableOpacity>
				</View>
				
				{showInfo && (
					<View style={styles.infoExpanded}>
						<NativeText style={styles.infoText}>
							The Readiness Check measures your cognitive state using Peak Alpha Frequency (PAF). 
							A higher PAF generally means your brain is more prepared for demanding work.
						</NativeText>
					</View>
				)}
			</View>
			
			<TouchableOpacity 
				style={styles.primaryButton}
				onPress={() => setCurrentPhase('connection')}
			>
				<NativeText style={styles.primaryButtonText}>Start Readiness Check</NativeText>
			</TouchableOpacity>
		</View>
	);
	
	const renderConnectionPage = () => (
		<View style={styles.card}>
			<View style={styles.iconContainer}>
				<View style={[styles.iconCircle, { backgroundColor: '#8B5CF6' }]}>
					<NativeText style={styles.iconText}>📡</NativeText>
				</View>
			</View>
			
			<NativeText style={styles.pageTitle}>Device Connection</NativeText>
			<NativeText style={styles.pageSubtitle}>
				{connectionStep === 'discover' && 'Connect to your Guardian EEG device'}
				{connectionStep === 'selecting' && 'Select your device from the list'}
				{connectionStep === 'connecting' && 'Establishing connection...'}
				{connectionStep === 'connected' && 'Connection successful!'}
			</NativeText>
			
			{connectionError && (
				<View style={styles.errorBanner}>
					<NativeText style={styles.errorText}>{connectionError}</NativeText>
				</View>
			)}
			
			{connectionStep === 'discover' && (
				<TouchableOpacity 
					style={[styles.primaryButton, styles.purpleButton, isScanning && styles.disabledButton]}
					onPress={scanForDevices}
					disabled={isScanning}
				>
					{isScanning ? (
						<View style={styles.buttonRow}>
							<ActivityIndicator color="#fff" size="small" />
							<NativeText style={styles.primaryButtonText}>  Discovering Devices...</NativeText>
						</View>
					) : (
						<NativeText style={styles.primaryButtonText}>Discover Devices</NativeText>
					)}
				</TouchableOpacity>
			)}
			
			{connectionStep === 'selecting' && (
				<View>
					<ScrollView style={styles.deviceList}>
						{devices.map((device) => (
							<TouchableOpacity
								key={device.address}
								style={[
									styles.deviceItem,
									selectedDevice?.address === device.address && styles.deviceItemSelected
								]}
								onPress={() => setSelectedDevice(device)}
							>
								<View>
									<NativeText style={styles.deviceName}>{device.name}</NativeText>
									<NativeText style={styles.deviceInfo}>
										Signal: {device.rssi ? `${Math.min(100, Math.max(0, 100 + device.rssi))}%` : 'N/A'}
									</NativeText>
								</View>
								{selectedDevice?.address === device.address && (
									<View style={styles.checkmark}>
										<NativeText style={styles.checkmarkText}>✓</NativeText>
									</View>
								)}
							</TouchableOpacity>
						))}
					</ScrollView>
					
					<TouchableOpacity 
						style={[styles.primaryButton, styles.purpleButton, (!selectedDevice || isConnecting) && styles.disabledButton]}
						onPress={connectToDevice}
						disabled={!selectedDevice || isConnecting}
					>
						<NativeText style={styles.primaryButtonText}>
							{isConnecting ? 'Connecting...' : 'Connect'}
						</NativeText>
					</TouchableOpacity>
					
					<TouchableOpacity 
						style={styles.secondaryButton}
						onPress={scanForDevices}
					>
						<NativeText style={styles.secondaryButtonText}>Scan Again</NativeText>
					</TouchableOpacity>
				</View>
			)}
			
			{connectionStep === 'connecting' && (
				<View style={styles.centerContent}>
					<ActivityIndicator color="#8B5CF6" size="large" />
					<NativeText style={styles.connectingText}>Connecting to {selectedDevice?.name}...</NativeText>
				</View>
			)}
			
			{connectionStep === 'connected' && (
				<View>
					<View style={styles.successCard}>
						<View style={styles.successIcon}>
							<NativeText style={styles.successIconText}>✓</NativeText>
						</View>
						<NativeText style={styles.successTitle}>Successfully Connected</NativeText>
						<NativeText style={styles.successSubtitle}>{selectedDevice?.name}</NativeText>
					</View>
					
					<TouchableOpacity 
						style={[styles.primaryButton, styles.purpleButton]}
						onPress={() => setCurrentPhase('impedance')}
					>
						<NativeText style={styles.primaryButtonText}>Move to Impedance Check</NativeText>
					</TouchableOpacity>
				</View>
			)}
		</View>
	);
	
	const renderImpedancePage = () => (
		<View style={styles.card}>
			<View style={styles.iconContainer}>
				<View style={[styles.iconCircle, { backgroundColor: '#14B8A6' }]}>
					<NativeText style={styles.iconText}>📊</NativeText>
				</View>
			</View>
			
			<NativeText style={styles.pageTitle}>Impedance Check</NativeText>
			<NativeText style={styles.pageSubtitle}>
				{impedanceStep === 'ready' && 'Verify signal quality before recording'}
				{impedanceStep === 'checking' && `Measuring impedance... ${impedanceCountdown}s`}
				{impedanceStep === 'complete' && 'Impedance check complete'}
			</NativeText>
			
			{impedanceStep === 'ready' && (
				<TouchableOpacity 
					style={[styles.primaryButton, styles.tealButton]}
					onPress={startImpedanceCheck}
				>
					<NativeText style={styles.primaryButtonText}>Start Impedance Check</NativeText>
				</TouchableOpacity>
			)}
			
			{impedanceStep === 'checking' && (
				<View style={styles.impedanceDisplay}>
					<View style={styles.countdownCircle}>
						<NativeText style={styles.countdownNumber}>{impedanceCountdown}</NativeText>
					</View>
					
					{currentImpedance !== null && (
						<View style={styles.currentImpedance}>
							<NativeText style={styles.impedanceLabel}>Current Reading</NativeText>
							<NativeText style={[
								styles.impedanceValue,
								currentImpedance < IMPEDANCE_THRESHOLD ? styles.goodValue : styles.poorValue
							]}>
								{currentImpedance.toFixed(1)} kΩ
							</NativeText>
						</View>
					)}
					
					<View style={styles.progressBar}>
						<View style={[styles.progressFill, { width: `${((10 - impedanceCountdown) / 10) * 100}%` }]} />
					</View>
				</View>
			)}
			
			{impedanceStep === 'complete' && impedanceStats && (
				<View>
					<View style={[
						styles.resultCard,
						impedanceStats.quality === 'good' ? styles.goodResultCard : styles.poorResultCard
					]}>
						<View style={[
							styles.resultIcon,
							impedanceStats.quality === 'good' ? styles.goodResultIcon : styles.poorResultIcon
						]}>
							<NativeText style={styles.resultIconText}>
								{impedanceStats.quality === 'good' ? '✓' : '!'}
							</NativeText>
						</View>
						
						<NativeText style={styles.resultTitle}>
							Mean Impedance: {impedanceStats.mean_kohm != null && impedanceStats.mean_kohm > 0 ? impedanceStats.mean_kohm.toFixed(1) : 'N/A'} kΩ
						</NativeText>
						<NativeText style={[
							styles.resultSubtitle,
							impedanceStats.quality === 'good' ? styles.goodText : styles.poorText
						]}>
							{impedanceStats.quality === 'good' ? 'Good Quality' : 'Poor Quality'}
						</NativeText>
						
						{impedanceStats.quality === 'poor' && (
							<NativeText style={styles.warningText}>
								Signal quality is lower than optimal ({'>'}300 kΩ). You may proceed, but consider adjusting the device for better results.
							</NativeText>
						)}
					</View>
					
					<TouchableOpacity 
						style={[styles.primaryButton, styles.tealButton, isStartingEEG && styles.disabledButton]}
						onPress={proceedToRecording}
						disabled={isStartingEEG}
					>
						<NativeText style={styles.primaryButtonText}>
							{isStartingEEG ? 'Starting EEG...' : 'Move to Recording'}
						</NativeText>
					</TouchableOpacity>
					
					<TouchableOpacity 
						style={styles.secondaryButton}
						onPress={() => {
							setImpedanceStep('ready');
							setImpedanceStats(null);
						}}
					>
						<NativeText style={styles.secondaryButtonText}>Check Again</NativeText>
					</TouchableOpacity>
				</View>
			)}
		</View>
	);
	
	const renderRecordingPage = () => (
		<View style={styles.card}>
			<View style={styles.iconContainer}>
				<View style={[styles.iconCircle, { backgroundColor: '#6366F1' }]}>
					<NativeText style={styles.iconText}>🧠</NativeText>
				</View>
			</View>
			
			<NativeText style={styles.pageTitle}>
				{recordingStep === 'setup' && 'Protocol Setup'}
				{recordingStep === 'instructions_pre' && 'Pre-Baseline'}
				{recordingStep === 'eyes_closed_pre' && 'Pre-Baseline Recording'}
				{recordingStep === 'intervention' && 'Intervention'}
				{recordingStep === 'instructions_post' && 'Post-Baseline'}
				{recordingStep === 'eyes_closed_post' && 'Post-Baseline Recording'}
				{recordingStep === 'complete' && 'Complete'}
			</NativeText>
			
			{recordingStep === 'setup' && (
				<View>
					<View style={styles.inputContainer}>
						<NativeText style={styles.inputLabel}>Intervention (optional)</NativeText>
						<TextInput
							style={styles.textInput}
							placeholder="e.g., Breathing exercise, Meditation"
							value={intervention}
							onChangeText={setIntervention}
							placeholderTextColor="#9CA3AF"
						/>
						<NativeText style={styles.inputHint}>
							Add an intervention to compare before/after states
						</NativeText>
					</View>
					
					<View style={styles.protocolSummary}>
						<NativeText style={styles.summaryTitle}>Protocol Flow:</NativeText>
						<NativeText style={styles.summaryItem}>1. Eyes closed - 30 seconds (pre-baseline)</NativeText>
						<NativeText style={styles.summaryItem}>2. Intervention - 60 seconds</NativeText>
						<NativeText style={styles.summaryItem}>3. Eyes closed - 30 seconds (post-baseline)</NativeText>
					</View>
					
					<TouchableOpacity 
						style={[styles.primaryButton, styles.indigoButton]}
						onPress={startProtocol}
					>
						<NativeText style={styles.primaryButtonText}>▶ Start Protocol</NativeText>
					</TouchableOpacity>
				</View>
			)}
			
			{recordingStep === 'instructions_pre' && (
				<View style={styles.instructionCard}>
					<NativeText style={styles.instructionTitle}>😌 Close Your Eyes</NativeText>
					<NativeText style={styles.instructionText}>
						Relax and remain still for the pre-baseline recording
					</NativeText>
					<View style={styles.countdownCircle}>
						<NativeText style={styles.countdownNumber}>{countdown}</NativeText>
					</View>
					<NativeText style={styles.protocolMessage}>{protocolMessage}</NativeText>
				</View>
			)}
			
			{recordingStep === 'eyes_closed_pre' && (
				<View style={styles.recordingDisplay}>
					<NativeText style={styles.phaseTitle}>😌 Keep Eyes Closed</NativeText>
					<NativeText style={styles.phaseSubtitle}>Pre-Baseline Recording</NativeText>
					<View style={styles.timerCircle}>
						<NativeText style={styles.timerText}>{recordingTime}s</NativeText>
					</View>
					<NativeText style={styles.phaseHint}>Stay relaxed...</NativeText>
				</View>
			)}
			
			{recordingStep === 'intervention' && (
				<View style={styles.recordingDisplay}>
					<NativeText style={styles.phaseTitle}>🧘 Intervention Time</NativeText>
					{intervention ? (
						<NativeText style={styles.interventionLabel}>{intervention}</NativeText>
					) : (
						<NativeText style={styles.interventionLabel}>Recording in progress</NativeText>
					)}
					<View style={styles.timerCircle}>
						<NativeText style={styles.timerText}>{recordingTime}s</NativeText>
					</View>
					<NativeText style={styles.phaseHint}>Complete your intervention...</NativeText>
				</View>
			)}
			
			{recordingStep === 'instructions_post' && (
				<View style={styles.instructionCard}>
					<NativeText style={styles.instructionTitle}>😌 Close Your Eyes Again</NativeText>
					<NativeText style={styles.instructionText}>
						Relax for the post-baseline recording
					</NativeText>
					<View style={styles.countdownCircle}>
						<NativeText style={styles.countdownNumber}>{countdown}</NativeText>
					</View>
					<NativeText style={styles.protocolMessage}>{protocolMessage}</NativeText>
				</View>
			)}
			
			{recordingStep === 'eyes_closed_post' && (
				<View style={styles.recordingDisplay}>
					<NativeText style={styles.phaseTitle}>😌 Keep Eyes Closed</NativeText>
					<NativeText style={styles.phaseSubtitle}>Post-Baseline Recording</NativeText>
					<View style={styles.timerCircle}>
						<NativeText style={styles.timerText}>{recordingTime}s</NativeText>
					</View>
					<NativeText style={styles.phaseHint}>Almost done...</NativeText>
				</View>
			)}
			
			{recordingStep === 'complete' && (
				<View style={styles.centerContent}>
					<ActivityIndicator color="#6366F1" size="large" />
					<NativeText style={styles.savingText}>Saving and analyzing...</NativeText>
				</View>
			)}
		</View>
	);
	
	const renderResultPage = () => {
		if (hasError) {
			return (
				<View style={styles.card}>
					<View style={styles.iconContainer}>
						<View style={[styles.iconCircle, { backgroundColor: '#EF4444' }]}>
							<NativeText style={styles.iconText}>✕</NativeText>
						</View>
					</View>
					
					<NativeText style={styles.pageTitle}>Error Occurred</NativeText>
					<NativeText style={styles.errorMessage}>{errorMessage}</NativeText>
					
					<TouchableOpacity 
						style={[styles.primaryButton, styles.grayButton]}
						onPress={resetApp}
					>
						<NativeText style={styles.primaryButtonText}>🔄 Try Again</NativeText>
					</TouchableOpacity>
				</View>
			);
		}
		
		if (hasIntervention && preScore !== null && postScore !== null) {
			const improvement = postScore - preScore;
			const improvementPercent = preScore !== 0 ? ((improvement / preScore) * 100).toFixed(0) : '0';
			
			return (
				<View style={styles.card}>
					<View style={styles.iconContainer}>
						<View style={[styles.iconCircle, { backgroundColor: '#10B981' }]}>
							<NativeText style={styles.iconText}>✓</NativeText>
						</View>
					</View>
					
					<NativeText style={styles.pageTitle}>Intervention Results</NativeText>
					<NativeText style={styles.pageSubtitle}>{intervention}</NativeText>
					
					<View style={styles.scoresCard}>
						<View style={styles.scoreRow}>
							<NativeText style={styles.scoreLabel}>Pre-Intervention</NativeText>
							<NativeText style={styles.scoreValue}>{preScore.toFixed(1)} Hz</NativeText>
						</View>
						<View style={styles.scoreBar}>
							<View style={[styles.scoreBarFill, styles.preScoreBar, { width: `${Math.min(100, (preScore / 15) * 100)}%` }]} />
						</View>
						
						<View style={styles.scoreRow}>
							<NativeText style={styles.scoreLabel}>Post-Intervention</NativeText>
							<NativeText style={styles.scoreValue}>{postScore.toFixed(1)} Hz</NativeText>
						</View>
						<View style={styles.scoreBar}>
							<View style={[styles.scoreBarFill, styles.postScoreBar, { width: `${Math.min(100, (postScore / 15) * 100)}%` }]} />
						</View>
						
						<View style={styles.improvementRow}>
							<NativeText style={styles.improvementLabel}>Change</NativeText>
							<NativeText style={[styles.improvementValue, improvement >= 0 ? styles.positiveChange : styles.negativeChange]}>
								{improvement >= 0 ? '+' : ''}{improvement.toFixed(2)} Hz ({improvementPercent}%)
							</NativeText>
						</View>
					</View>
					
					<View style={[styles.resultSummary, improvement >= 0 ? styles.positiveSummary : styles.negativeSummary]}>
						<NativeText style={styles.resultSummaryText}>
							{improvement > 0 
								? 'Your intervention had a positive effect on your readiness state!'
								: improvement < 0
								? 'Your readiness decreased slightly. Consider trying a different intervention.'
								: 'No significant change detected.'}
						</NativeText>
					</View>
					
					<TouchableOpacity 
						style={[styles.primaryButton, styles.greenButton]}
						onPress={resetApp}
					>
						<NativeText style={styles.primaryButtonText}>🔄 Start New Check</NativeText>
					</TouchableOpacity>
				</View>
			);
		}
		
		// Simple ready/not ready result
		const isReady = readinessScore !== null && readinessScore >= 9;
		
		return (
			<View style={styles.card}>
				<View style={styles.iconContainer}>
					<View style={[styles.iconCircle, { backgroundColor: isReady ? '#10B981' : '#F59E0B' }]}>
						<NativeText style={styles.iconText}>{isReady ? '✓' : '!'}</NativeText>
					</View>
				</View>
				
				<NativeText style={styles.pageTitle}>
					{isReady ? 'Ready for Deep Work' : 'Not Ready for Deep Work'}
				</NativeText>
				<NativeText style={styles.pageSubtitle}>
					{isReady 
						? 'Your cognitive state is optimal for focused work'
						: 'Consider taking a short break or doing a quick intervention'}
				</NativeText>
				
				<View style={styles.readinessCard}>
					<NativeText style={styles.readinessLabel}>Readiness Score (IAF)</NativeText>
					<NativeText style={[styles.readinessValue, isReady ? styles.readyValue : styles.notReadyValue]}>
						{readinessScore !== null ? readinessScore.toFixed(1) : 'N/A'} Hz
					</NativeText>
					<View style={styles.readinessBar}>
						<View style={[
							styles.readinessBarFill, 
							isReady ? styles.readyBarFill : styles.notReadyBarFill,
							{ width: `${Math.min(100, ((readinessScore || 0) / 15) * 100)}%` }
						]} />
					</View>
				</View>
				
				{!isReady && (
					<View style={styles.suggestionsCard}>
						<NativeText style={styles.suggestionsTitle}>Suggestions:</NativeText>
						<NativeText style={styles.suggestionItem}>• Take a 5-minute breathing exercise</NativeText>
						<NativeText style={styles.suggestionItem}>• Go for a short walk</NativeText>
						<NativeText style={styles.suggestionItem}>• Try a brief meditation session</NativeText>
					</View>
				)}
				
				<TouchableOpacity 
					style={[styles.primaryButton, isReady ? styles.greenButton : styles.amberButton]}
					onPress={resetApp}
				>
					<NativeText style={styles.primaryButtonText}>🔄 Start New Check</NativeText>
				</TouchableOpacity>
			</View>
		);
	};
	
	// Main render
	return (
		<ScrollView style={styles.container} contentContainerStyle={styles.contentContainer}>
			{currentPhase === 'reminder' && renderReminderPage()}
			{currentPhase === 'connection' && renderConnectionPage()}
			{currentPhase === 'impedance' && renderImpedancePage()}
			{currentPhase === 'recording' && renderRecordingPage()}
			{currentPhase === 'result' && renderResultPage()}
		</ScrollView>
	);
}

const styles = StyleSheet.create({
	container: {
		flex: 1,
		backgroundColor: '#F1F5F9',
	},
	contentContainer: {
		padding: 20,
		paddingTop: 60,
	},
	card: {
		backgroundColor: '#FFFFFF',
		borderRadius: 24,
		padding: 24,
		shadowColor: '#000',
		shadowOffset: { width: 0, height: 4 },
		shadowOpacity: 0.1,
		shadowRadius: 12,
		elevation: 5,
	},
	iconContainer: {
		alignItems: 'center',
		marginBottom: 16,
	},
	iconCircle: {
		width: 64,
		height: 64,
		borderRadius: 16,
		justifyContent: 'center',
		alignItems: 'center',
	},
	iconText: {
		fontSize: 28,
	},
	notificationCard: {
		backgroundColor: '#EFF6FF',
		borderRadius: 16,
		padding: 20,
		marginBottom: 20,
	},
	notificationHeader: {
		flexDirection: 'row',
	},
	notificationTime: {
		color: '#2563EB',
		fontSize: 14,
		marginBottom: 4,
	},
	notificationTitle: {
		fontSize: 18,
		fontWeight: '600',
		color: '#1E293B',
		marginBottom: 8,
	},
	notificationDesc: {
		color: '#64748B',
		fontSize: 14,
		lineHeight: 20,
	},
	infoButton: {
		width: 32,
		height: 32,
		borderRadius: 16,
		backgroundColor: '#FFFFFF',
		justifyContent: 'center',
		alignItems: 'center',
		marginLeft: 8,
	},
	infoButtonText: {
		color: '#2563EB',
		fontSize: 16,
		fontWeight: '600',
	},
	infoExpanded: {
		marginTop: 12,
		paddingTop: 12,
		borderTopWidth: 1,
		borderTopColor: '#BFDBFE',
	},
	infoText: {
		color: '#64748B',
		fontSize: 14,
		lineHeight: 20,
	},
	primaryButton: {
		backgroundColor: '#2563EB',
		paddingVertical: 16,
		borderRadius: 12,
		alignItems: 'center',
		marginTop: 8,
	},
	primaryButtonText: {
		color: '#FFFFFF',
		fontSize: 16,
		fontWeight: '600',
	},
	purpleButton: {
		backgroundColor: '#8B5CF6',
	},
	tealButton: {
		backgroundColor: '#14B8A6',
	},
	indigoButton: {
		backgroundColor: '#6366F1',
	},
	greenButton: {
		backgroundColor: '#10B981',
	},
	amberButton: {
		backgroundColor: '#F59E0B',
	},
	grayButton: {
		backgroundColor: '#64748B',
	},
	disabledButton: {
		opacity: 0.5,
	},
	secondaryButton: {
		paddingVertical: 12,
		alignItems: 'center',
		marginTop: 8,
	},
	secondaryButtonText: {
		color: '#64748B',
		fontSize: 14,
	},
	buttonRow: {
		flexDirection: 'row',
		alignItems: 'center',
	},
	pageTitle: {
		fontSize: 20,
		fontWeight: '600',
		color: '#1E293B',
		textAlign: 'center',
		marginBottom: 8,
	},
	pageSubtitle: {
		fontSize: 14,
		color: '#64748B',
		textAlign: 'center',
		marginBottom: 20,
	},
	errorBanner: {
		backgroundColor: '#FEE2E2',
		padding: 12,
		borderRadius: 8,
		marginBottom: 16,
	},
	errorText: {
		color: '#DC2626',
		fontSize: 14,
		textAlign: 'center',
	},
	deviceList: {
		maxHeight: 250,
		marginBottom: 16,
	},
	deviceItem: {
		flexDirection: 'row',
		justifyContent: 'space-between',
		alignItems: 'center',
		padding: 16,
		borderRadius: 12,
		borderWidth: 2,
		borderColor: '#E2E8F0',
		marginBottom: 8,
		backgroundColor: '#FFFFFF',
	},
	deviceItemSelected: {
		borderColor: '#8B5CF6',
		backgroundColor: '#F5F3FF',
	},
	deviceName: {
		fontSize: 16,
		fontWeight: '500',
		color: '#1E293B',
	},
	deviceInfo: {
		fontSize: 13,
		color: '#64748B',
		marginTop: 2,
	},
	checkmark: {
		width: 24,
		height: 24,
		borderRadius: 12,
		backgroundColor: '#8B5CF6',
		justifyContent: 'center',
		alignItems: 'center',
	},
	checkmarkText: {
		color: '#FFFFFF',
		fontSize: 14,
		fontWeight: '600',
	},
	centerContent: {
		alignItems: 'center',
		paddingVertical: 32,
	},
	connectingText: {
		marginTop: 16,
		color: '#64748B',
		fontSize: 14,
	},
	successCard: {
		backgroundColor: '#ECFDF5',
		borderWidth: 2,
		borderColor: '#A7F3D0',
		borderRadius: 12,
		padding: 24,
		alignItems: 'center',
		marginBottom: 16,
	},
	successIcon: {
		width: 48,
		height: 48,
		borderRadius: 24,
		backgroundColor: '#10B981',
		justifyContent: 'center',
		alignItems: 'center',
		marginBottom: 12,
	},
	successIconText: {
		color: '#FFFFFF',
		fontSize: 24,
		fontWeight: '600',
	},
	successTitle: {
		fontSize: 16,
		fontWeight: '600',
		color: '#065F46',
		marginBottom: 4,
	},
	successSubtitle: {
		fontSize: 14,
		color: '#047857',
	},
	impedanceDisplay: {
		alignItems: 'center',
		paddingVertical: 20,
	},
	countdownCircle: {
		width: 100,
		height: 100,
		borderRadius: 50,
		backgroundColor: '#F0FDFA',
		borderWidth: 4,
		borderColor: '#14B8A6',
		justifyContent: 'center',
		alignItems: 'center',
		marginBottom: 20,
	},
	countdownNumber: {
		fontSize: 36,
		fontWeight: '700',
		color: '#14B8A6',
	},
	currentImpedance: {
		alignItems: 'center',
		marginBottom: 20,
	},
	impedanceLabel: {
		fontSize: 14,
		color: '#64748B',
		marginBottom: 4,
	},
	impedanceValue: {
		fontSize: 28,
		fontWeight: '700',
	},
	goodValue: {
		color: '#10B981',
	},
	poorValue: {
		color: '#F59E0B',
	},
	progressBar: {
		width: '100%',
		height: 8,
		backgroundColor: '#E2E8F0',
		borderRadius: 4,
		overflow: 'hidden',
	},
	progressFill: {
		height: '100%',
		backgroundColor: '#14B8A6',
		borderRadius: 4,
	},
	resultCard: {
		borderRadius: 12,
		padding: 24,
		alignItems: 'center',
		marginBottom: 16,
		borderWidth: 2,
	},
	goodResultCard: {
		backgroundColor: '#ECFDF5',
		borderColor: '#A7F3D0',
	},
	poorResultCard: {
		backgroundColor: '#FFFBEB',
		borderColor: '#FDE68A',
	},
	resultIcon: {
		width: 48,
		height: 48,
		borderRadius: 24,
		justifyContent: 'center',
		alignItems: 'center',
		marginBottom: 12,
	},
	goodResultIcon: {
		backgroundColor: '#10B981',
	},
	poorResultIcon: {
		backgroundColor: '#F59E0B',
	},
	resultIconText: {
		color: '#FFFFFF',
		fontSize: 24,
		fontWeight: '600',
	},
	resultTitle: {
		fontSize: 16,
		fontWeight: '600',
		color: '#1E293B',
		marginBottom: 4,
	},
	resultSubtitle: {
		fontSize: 14,
		fontWeight: '500',
	},
	goodText: {
		color: '#047857',
	},
	poorText: {
		color: '#B45309',
	},
	warningText: {
		marginTop: 12,
		fontSize: 13,
		color: '#92400E',
		textAlign: 'center',
		lineHeight: 18,
	},
	inputContainer: {
		marginBottom: 20,
	},
	inputLabel: {
		fontSize: 14,
		fontWeight: '500',
		color: '#374151',
		marginBottom: 8,
	},
	textInput: {
		borderWidth: 2,
		borderColor: '#E2E8F0',
		borderRadius: 12,
		paddingHorizontal: 16,
		paddingVertical: 12,
		fontSize: 16,
		color: '#1E293B',
	},
	inputHint: {
		marginTop: 8,
		fontSize: 13,
		color: '#9CA3AF',
	},
	instructionCard: {
		backgroundColor: '#EEF2FF',
		borderRadius: 12,
		padding: 24,
		alignItems: 'center',
		marginBottom: 16,
	},
	instructionTitle: {
		fontSize: 18,
		fontWeight: '600',
		color: '#1E293B',
		marginBottom: 8,
	},
	instructionText: {
		fontSize: 14,
		color: '#64748B',
		textAlign: 'center',
	},
	protocolMessage: {
		marginTop: 12,
		fontSize: 13,
		color: '#6366F1',
		textAlign: 'center',
	},
	recordingDisplay: {
		alignItems: 'center',
		paddingVertical: 20,
	},
	phaseTitle: {
		fontSize: 20,
		fontWeight: '600',
		color: '#1E293B',
		marginBottom: 20,
	},
	timerCircle: {
		width: 120,
		height: 120,
		borderRadius: 60,
		backgroundColor: '#EEF2FF',
		borderWidth: 6,
		borderColor: '#6366F1',
		justifyContent: 'center',
		alignItems: 'center',
		marginBottom: 20,
	},
	timerText: {
		fontSize: 32,
		fontWeight: '700',
		color: '#6366F1',
	},
	phaseHint: {
		fontSize: 14,
		color: '#64748B',
	},
	phaseSubtitle: {
		fontSize: 14,
		color: '#64748B',
		marginBottom: 16,
	},
	interventionLabel: {
		fontSize: 16,
		color: '#6366F1',
		fontWeight: '500',
		marginBottom: 16,
	},
	protocolSummary: {
		backgroundColor: '#F8FAFC',
		borderRadius: 12,
		padding: 16,
		marginBottom: 16,
	},
	summaryTitle: {
		fontSize: 14,
		fontWeight: '600',
		color: '#1E293B',
		marginBottom: 8,
	},
	summaryItem: {
		fontSize: 13,
		color: '#64748B',
		marginBottom: 4,
	},
	interventionCard: {
		backgroundColor: '#EEF2FF',
		borderRadius: 12,
		padding: 20,
		marginBottom: 16,
	},
	interventionTitle: {
		fontSize: 16,
		fontWeight: '600',
		color: '#4338CA',
		marginBottom: 8,
	},
	interventionText: {
		fontSize: 14,
		color: '#64748B',
		lineHeight: 20,
	},
	savingText: {
		marginTop: 16,
		fontSize: 14,
		color: '#64748B',
	},
	errorMessage: {
		fontSize: 14,
		color: '#64748B',
		textAlign: 'center',
		marginBottom: 20,
	},
	scoresCard: {
		backgroundColor: '#F8FAFC',
		borderRadius: 12,
		padding: 20,
		marginBottom: 16,
	},
	scoreRow: {
		flexDirection: 'row',
		justifyContent: 'space-between',
		alignItems: 'center',
		marginBottom: 8,
	},
	scoreLabel: {
		fontSize: 14,
		color: '#64748B',
	},
	scoreValue: {
		fontSize: 16,
		fontWeight: '600',
		color: '#1E293B',
	},
	scoreBar: {
		height: 12,
		backgroundColor: '#E2E8F0',
		borderRadius: 6,
		marginBottom: 16,
		overflow: 'hidden',
	},
	scoreBarFill: {
		height: '100%',
		borderRadius: 6,
	},
	preScoreBar: {
		backgroundColor: '#F59E0B',
	},
	postScoreBar: {
		backgroundColor: '#10B981',
	},
	improvementRow: {
		flexDirection: 'row',
		justifyContent: 'space-between',
		alignItems: 'center',
		paddingTop: 16,
		borderTopWidth: 1,
		borderTopColor: '#E2E8F0',
	},
	improvementLabel: {
		fontSize: 14,
		color: '#64748B',
	},
	improvementValue: {
		fontSize: 16,
		fontWeight: '600',
	},
	positiveChange: {
		color: '#10B981',
	},
	negativeChange: {
		color: '#EF4444',
	},
	resultSummary: {
		borderRadius: 12,
		padding: 16,
		marginBottom: 16,
	},
	positiveSummary: {
		backgroundColor: '#ECFDF5',
	},
	negativeSummary: {
		backgroundColor: '#FEF3C7',
	},
	resultSummaryText: {
		fontSize: 14,
		color: '#1E293B',
		textAlign: 'center',
		lineHeight: 20,
	},
	readinessCard: {
		backgroundColor: '#F8FAFC',
		borderRadius: 12,
		padding: 20,
		alignItems: 'center',
		marginBottom: 16,
	},
	readinessLabel: {
		fontSize: 14,
		color: '#64748B',
		marginBottom: 8,
	},
	readinessValue: {
		fontSize: 36,
		fontWeight: '700',
		marginBottom: 12,
	},
	readyValue: {
		color: '#10B981',
	},
	notReadyValue: {
		color: '#F59E0B',
	},
	readinessBar: {
		width: '100%',
		height: 12,
		backgroundColor: '#E2E8F0',
		borderRadius: 6,
		overflow: 'hidden',
	},
	readinessBarFill: {
		height: '100%',
		borderRadius: 6,
	},
	readyBarFill: {
		backgroundColor: '#10B981',
	},
	notReadyBarFill: {
		backgroundColor: '#F59E0B',
	},
	suggestionsCard: {
		backgroundColor: '#FFFBEB',
		borderRadius: 12,
		padding: 16,
		marginBottom: 16,
	},
	suggestionsTitle: {
		fontSize: 14,
		fontWeight: '600',
		color: '#92400E',
		marginBottom: 8,
	},
	suggestionItem: {
		fontSize: 14,
		color: '#B45309',
		marginBottom: 4,
	},
});
