# IDUN Guardian EEG - Frontend App

React Native mobile application for real-time EEG data visualization and Peak Alpha Frequency (PAF) assessment.

## Quick Start

See the main [project README](../README.md) for complete setup instructions.

### Installation

```bash
npm install
```

### Running the App

**Start development server:**
```bash
npm start
```

**Open in iOS Simulator:**
```bash
npm run ios
```

**Open in Android Emulator:**
```bash
npm run android
```

## Configuration

Create a `.env` file in this directory:

```bash
# Copy the example file
cp env.example .env
```

Then edit `.env` to configure your API URL:

- **iOS Simulator:** `EXPO_PUBLIC_API_URL=http://localhost:8000`
- **Android Emulator:** `EXPO_PUBLIC_API_URL=http://10.0.2.2:8000`
- **Physical Device:** `EXPO_PUBLIC_API_URL=http://<YOUR_COMPUTER_IP>:8000`

## Project Structure

```
frontend-app/
├── app/                    # Application screens
│   └── (tabs)/            # Tab navigation
│       ├── index.tsx      # Home/Welcome screen
│       └── demo.tsx       # Main EEG streaming screen
├── components/            # Reusable UI components
├── constants/            # Configuration constants
│   ├── api.ts           # API endpoint configuration
│   └── theme.ts         # Theme colors and styles
├── hooks/               # Custom React hooks
└── assets/             # Images and fonts
```

## Key Features

- **Real-time EEG Visualization:** Live chart updates via WebSocket
- **PAF Assessment:** Standardized Peak Alpha Frequency protocol
- **Device Connection:** Connect to IDUN Guardian earbuds
- **Impedance Monitoring:** Real-time electrode quality feedback
- **Test Mode:** Works without physical device using simulated data

## Development

This app uses:
- **Expo Router** for file-based navigation
- **TypeScript** for type safety
- **WebSocket** for real-time data streaming
- **D3.js** (via react-native-svg) for charts

### Available Scripts

- `npm start` - Start Expo development server
- `npm run ios` - Open in iOS Simulator
- `npm run android` - Open in Android Emulator
- `npm run web` - Open in web browser (limited support)

### API Integration

The app connects to the backend server defined in `constants/api.ts`. All WebSocket and REST endpoints are automatically configured based on the `EXPO_PUBLIC_API_URL` environment variable.

## Troubleshooting

See the main [project README](../README.md#troubleshooting) for detailed troubleshooting steps.

## Learn More

- [Expo documentation](https://docs.expo.dev/)
- [React Native documentation](https://reactnative.dev/)
- [Expo Router documentation](https://docs.expo.dev/router/introduction/)
