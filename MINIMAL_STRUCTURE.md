# Minimal Demo Structure

This repository has been cleaned to contain **ONLY** what's needed for the demo.

## Current Structure

```
idun-lifestack-demo/
├── backend/                          # FastAPI backend server
│   ├── __init__.py
│   ├── __main__.py                  # Entry point: python -m backend
│   ├── server.py                    # Main FastAPI app with WebSocket
│   └── paf_protocol_service.py      # PAF protocol logic
│
├── frontend-app/                     # React Native mobile app
│   ├── app/                         # App screens (Expo Router)
│   ├── components/                  # UI components
│   ├── constants/                   # API config, theme
│   ├── hooks/                       # React hooks
│   ├── assets/                      # Images, fonts
│   ├── package.json                 # Node dependencies
│   ├── .env                         # API URL config
│   └── README.md                    # Frontend docs
│
├── paf_protocol/                     # PAF protocol modules (imported by backend)
│   ├── guardian_recording_protocol.py  # EEG recorder, GuardianController
│   └── paf_from_recording.py          # PAF computation
│
├── live_eeg_plot.py                  # Guardian device integration (imported by backend)
├── __init__.py                       # Root package init
├── requirements.txt                  # Python dependencies
├── .env                              # IDUN API token (DO NOT COMMIT!)
├── .env.example                      # Template for API token
├── .gitignore                        # Git ignore rules
├── README.md                         # Main documentation
├── LICENSE                           # MIT License
├── CONTRIBUTING.md                   # Contribution guidelines
└── grave2/                           # Unused files (can delete after testing)
```

## What's in grave2/ (Safe to Delete)

**Removed Files:**
- `run_simple_paf_readiness.py` - Dead code (was imported but never used)
- `forwarding/` - Standalone ngrok streaming tool (not part of demo)
- `paf_protocol/.env` - Duplicate API token
- `paf_protocol/recordings/` - Old recordings
- `paf_protocol/README.md` - Standalone tool docs
- `paf_protocol/requirements.txt` - Standalone tool requirements
- `OLD_README.md` - Old documentation
- `README_TOMA.md` - Quick reference
- `SETUP_SUMMARY.md` - Setup guide
- `ANALYSIS.md` - File analysis
- `recordings/` - Old recordings from root
- `__pycache__/` folders - Python cache
- `.claude/` - Editor settings

## What Changed

1. **Removed dead import** from `backend/server.py`:
   - Line 13 & 19: `from run_simple_paf_readiness import ...` (unused)

2. **Cleaned paf_protocol/** to only essential files:
   - Kept: `guardian_recording_protocol.py`, `paf_from_recording.py`
   - Removed: `.env`, `recordings/`, `README.md`, `requirements.txt`

3. **Moved all non-demo files** to `grave2/`

## How to Run

**Backend:**
```bash
conda activate idun-env
python -m backend
```

**Frontend:**
```bash
cd frontend-app
npm start
```

## File Dependencies

### Backend Import Chain
```
python -m backend
  └── backend/__main__.py
      └── backend/server.py
          ├── live_eeg_plot.py ✓
          ├── paf_protocol/paf_from_recording.py ✓
          └── backend/paf_protocol_service.py ✓
              └── paf_protocol/guardian_recording_protocol.py ✓
```

### Frontend
- Self-contained in `frontend-app/`
- No dependencies on root files
- Connects to backend via WebSocket/REST

## To Delete grave2/

Once you've tested and confirmed the demo works:
```bash
rm -rf grave2/
```

## Files You Can Still Customize

- `LICENSE` - Update copyright holder name
- `README.md` - Add screenshots, update descriptions
- `CONTRIBUTING.md` - Adjust contribution guidelines

---

**Everything not listed above has been removed or moved to grave2/.**
