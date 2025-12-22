# IDUN Guardian EEG Demo

Real-time EEG streaming from IDUN Guardian earbuds to a mobile app for brain activity visualization and Peak Alpha Frequency (PAF) assessment.

---

## 🚀 TL;DR Quick Start

**Prerequisites:** Python 3.8+, Node.js 22.14.0+, Expo Go, IDUN Guardian earbuds (optional)

### Checklist:

- [ ] **1. Get IDUN API Token** from [iduntechnologies.com](https://www.iduntechnologies.com/)
- [ ] **2. Configure Root `.env`** - Create `.env` in project root:
  ```bash
  IDUN_API_TOKEN=idun_your_token_here
  ```
- [ ] **3. Configure Frontend `.env`** - Create `frontend-app/.env`:
  ```bash
  # iOS Simulator
  EXPO_PUBLIC_API_URL=http://localhost:8000

  # Physical device (replace with your computer's IP)
  # EXPO_PUBLIC_API_URL=http://192.168.1.XXX:8000
  ```
- [ ] **4. Install Python dependencies:**
  ```bash
  pip install -r requirements.txt
  ```
- [ ] **5. Install Frontend dependencies:**
  ```bash
  cd frontend-app && npm install && cd ..
  ```
- [ ] **6. Run in 2 terminals:**

**Terminal 1 (Backend):**
```bash
python -m backend
```

**Terminal 2 (Frontend):**
```bash
cd frontend-app
npm start
# Press 'i' for iOS Simulator or 'a' for Android
```

---

## 📋 Detailed Setup

### Step 1: Get Your IDUN API Token

1. Visit [IDUN Technologies](https://www.iduntechnologies.com/)
2. Sign up/login → API settings
3. Copy your API token (starts with `idun_`)

### Step 2: Configure Environment Variables

**YOU NEED 2 SEPARATE `.env` FILES:**

#### A. Root `.env` (for backend)
Create in project root:
```bash
cp .env.example .env
# Edit .env and add:
IDUN_API_TOKEN=idun_your_actual_token_here
```

#### B. `frontend-app/.env` (for frontend)
Create in `frontend-app/` directory:
```bash
cd frontend-app
cp env.example .env
# Edit .env
```

**Choose based on your device:**
- **iOS Simulator:** `EXPO_PUBLIC_API_URL=http://localhost:8000`
- **Android Emulator:** `EXPO_PUBLIC_API_URL=http://10.0.2.2:8000`
- **Physical Device:** `EXPO_PUBLIC_API_URL=http://YOUR_COMPUTER_IP:8000`

**Find your computer's IP:**
- macOS/Linux: `ifconfig | grep "inet " | grep -v 127.0.0.1`
- Windows: `ipconfig` (look for IPv4 Address)

### Step 3: Install Dependencies

**Python (Backend):**
```bash
# Create virtual environment (optional but recommended)
conda create -n idun-env python=3.10
conda activate idun-env

# Install dependencies
pip install -r requirements.txt
```

**Node.js (Frontend):**
```bash
cd frontend-app
npm install
```

### Step 4: Run the Demo (2 Terminals Required!)

**Terminal 1 - Backend:**
```bash
# Activate environment if using one
conda activate idun-env

# Run backend
python -m backend
```
Expected output: `INFO:     Uvicorn running on http://0.0.0.0:8000`

**Terminal 2 - Frontend:**
```bash
cd frontend-app
npm start

# Then press:
# 'i' for iOS Simulator
# 'a' for Android Emulator
# Or scan QR code with Expo Go app on physical device
```

---

## 🎯 Using the App

1. **Connect Guardian** - Tap "Connect" (or app works in test mode without device)
2. **Start Streaming** - Tap "Start" to see live EEG data
3. **Run PAF Assessment** - Follow on-screen protocol (eyes closed → recording)
4. **View Results** - Data streams in real-time on chart

---

## 🏗️ Project Structure

```
idun-lifestack-demo/
├── backend/                    # FastAPI server
│   ├── __main__.py            # Entry point
│   ├── server.py              # WebSocket & REST API
│   └── paf_protocol_service.py
├── frontend-app/              # React Native (Expo)
│   ├── app/                   # Screens
│   ├── components/            # UI components
│   └── .env                   # ⚠️ API URL config
├── paf_protocol/              # PAF computation modules
├── live_eeg_plot.py          # Guardian device integration
├── requirements.txt           # Python deps
├── .env                       # ⚠️ IDUN API token
└── .env.example              # Template
```

---

## 🔧 Troubleshooting

### "IDUN_API_TOKEN not set" error
- Check root `.env` file exists and has correct token
- Restart backend after creating `.env`

### WebSocket connection fails
- **iOS Simulator:** Use `http://localhost:8000`
- **Physical device:**
  - Ensure computer & device on same WiFi
  - Use computer's IP (not localhost) in `frontend-app/.env`
  - Restart Expo after changing `.env`

### "Module not found" errors
- Make sure you're in project root when running `python -m backend`
- Activate Python environment: `conda activate idun-env`
- Reinstall: `pip install -r requirements.txt`

### Guardian connection fails
- Check earbuds are powered on & in Bluetooth range
- Verify API token is correct
- App works in test mode without device

### Port 8000 in use
```bash
# macOS/Linux
lsof -ti:8000 | xargs kill -9

# Windows
netstat -ano | findstr :8000
taskkill /PID <PID> /F
```

---

## 📚 API Endpoints

**REST:**
- `GET /health` - Health check
- `GET /connect` - Connect to Guardian
- `GET /start-streaming` - Start EEG stream
- `GET /stop-streaming` - Stop stream

**WebSocket:**
- `WS /data` - Real-time EEG data stream

---

## 🛠️ Development

**Backend:** Auto-reloads on file changes
**Frontend:** Hot reload with Expo (press `r` to reload manually)

**Configuration:**
- Backend settings: `live_eeg_plot.py` (sample rate, filters)
- Frontend API URL: `frontend-app/.env`

---

## 📝 Requirements Summary

| Component | Requirement |
|-----------|-------------|
| Python | 3.8+ |
| Node.js | 22.14.0+ |
| Guardian Device | Optional (test mode available) |
| IDUN API Token | Required for real device |
| 2 Terminals | Required |
| 2 .env files | Required (root + frontend-app) |

---

## 🤝 Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md)

## 📄 License

MIT License - see [LICENSE](LICENSE)

## 🙏 Acknowledgments

- IDUN Technologies for Guardian EEG SDK
- FastAPI & Expo communities
