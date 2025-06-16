# AlertCam Backend

AlertCam is a system for real-time vehicle detection, alarm management, and notification delivery (including Telegram integration). It is built with Flask, SQLAlchemy, and uses YOLO for vehicle detection.

## Features

- Real-time vehicle detection using YOLO and OpenCV.
- Alarm management for detected vehicles.
- Event logging (movement, disappearance).
- Telegram bot for notifications, history, and video retrieval.
- REST API for user management, alarm control, and event history.
- Video recording of alarm events.
- Configurable notification preferences.

## Requirements

- Python 3.10+
- pip (Python package manager)
- [Ultralytics YOLO](https://docs.ultralytics.com/)
- PostgreSQL or SQLite (default)
- Telegram Bot Token (for Telegram integration)

## Setup

1. **Clone the repository:**
   ```bash
   git clone <repo-url>
   cd alertcam-backend
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment variables:**
   - Copy `.env.example` to `.env` and edit as needed (database URI, Telegram bot token, etc).

4. **Run database migrations:**
   ```bash
   flask db upgrade
   ```

5. **Start the backend:**
   ```bash
   python run.py
   ```

## API Endpoints

- `POST /api/auth/register` – Register a new user
- `POST /api/auth/login` – Login and get JWT tokens
- `POST /api/alarms/<vehicle_track_id>` – Set alarm for a vehicle
- `DELETE /api/alarms/<alarm_id>` – Unset alarm
- `GET /api/alarms` – List active alarms
- `GET /api/vehicles/detected` – Get currently detected vehicles
- `GET /api/alarms/history` – Get alarm and event history
- `POST /api/user/telegram_verification_code` – Generate Telegram verification code
- `PUT /api/user/password` – Change user password
- `GET/PUT /api/user/notification_preferences` – Get or update notification preferences

## Telegram Bot

- Link your Telegram account using `/start` and a verification code from the mobile app.
- `/history` – View recent alarm events.
- `/video <event_id>` – Get video for a specific event.
- `/settings` – Manage notification preferences.
- `/stop` – Unlink your Telegram account.

## Configuration

All configuration is managed via environment variables or `.env` file. See `app/config.py` for available options.

## Development

- Code is organized as a Flask application factory.
- Migrations are managed with Flask-Migrate (Alembic).
- Detection runs in a separate process; notifications and video writing are handled by worker threads/processes.

## License

MIT License

---

**Note:** For YOLO model weights and RTSP camera setup, refer to the documentation of your camera and YOLO version.
