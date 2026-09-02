# stl2step Web Service

Web service for converting STL files to STEP format using the `stl2step` Python package.

## Features

- No registration required - session-based with random UUIDs
- Queue system with configurable parallel conversions
- Real-time status updates (queue position, processing, completed)
- Configurable time and memory limits per conversion
- Automatic file cleanup with keep-alive mechanism
- Simple drag-and-drop web interface

## Quick Start

### Using Docker Compose (Recommended)

```bash
docker-compose up -d
```

Then open http://localhost:8000

### Manual Installation

```bash
pip install -r requirements.txt
pip install stl2step
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Configuration

Edit `config/settings.yaml` to customize:

```yaml
server:
  host: "0.0.0.0"
  port: 8000

queue:
  max_parallel_conversions: 2
  max_queue_size: 100

conversion:
  timeout_seconds: 300
  memory_limit_mb: 2048

session:
  max_file_retention_seconds: 3600
  keep_alive_interval_seconds: 30
  cleanup_interval_seconds: 60

upload:
  max_file_size_mb: 100
  allowed_extensions: [".stl", ".STL"]

storage:
  temp_dir: "/tmp/stl2step_sessions"
  upload_dir: "/tmp/stl2step_uploads"
```

### Configuration Options

| Setting | Description | Default |
|---------|-------------|---------|
| `queue.max_parallel_conversions` | Number of simultaneous conversions | 2 |
| `queue.max_queue_size` | Maximum jobs in queue | 100 |
| `conversion.timeout_seconds` | Max time per conversion | 300 |
| `conversion.memory_limit_mb` | Memory limit per conversion | 2048 |
| `session.max_file_retention_seconds` | Max time to keep converted files | 3600 |
| `session.keep_alive_interval_seconds` | Client must ping at least this often | 30 |
| `session.cleanup_interval_seconds` | How often to check for expired sessions | 60 |
| `upload.max_file_size_mb` | Maximum upload size | 100 |

## API Endpoints

- `GET /` - Web interface
- `POST /upload` - Upload STL file (returns job ID)
- `GET /status/{job_id}` - Get job status
- `GET /jobs` - List all jobs for current session
- `GET /download/{job_id}` - Download converted STEP file
- `POST /keepalive` - Keep session alive
- `GET /api/stats` - Get queue statistics
- `GET /health` - Health check

## How It Works

1. User visits the site → gets a random session ID (stored in HttpOnly cookie)
2. User uploads STL file → file stored in session-specific directory
3. Job added to queue → user sees position in queue
4. Worker picks up job (respecting `max_parallel_conversions`)
5. Conversion runs in subprocess with resource limits
6. On completion, STEP file stored in session directory
7. User downloads file → file served from session directory
8. Keep-alive pings every 30s keep session alive
9. Cleanup removes expired sessions and files

## Session Isolation

Each user gets a unique session ID. Files are stored in:
- Uploads: `/tmp/stl2step_uploads/{session_id}/`
- Outputs: `/tmp/stl2step_sessions/{session_id}/`

Users cannot access other users' files.

## Resource Limits

Each conversion runs in a separate subprocess with:
- Virtual memory limit (RLIMIT_AS)
- CPU time limit (RLIMIT_CPU)
- Timeout enforcement via asyncio

## License

AGPL-3.0-or-later (same as stl2step)