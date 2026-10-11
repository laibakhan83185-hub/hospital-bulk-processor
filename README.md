# Hospital Bulk Processing API

A FastAPI service that validates CSV files and creates hospitals in bulk through the external Hospital Directory API.

**Live API:** https://hospital-bulk-processor-7hw2.onrender.com/docs

> The app runs on Render's free tier. The first request after a period of inactivity can take 30–60 seconds while the service wakes up.

## Features

- Upload a CSV file containing up to 20 hospitals.
- Validate required fields and reject duplicate hospitals.
- Create hospitals concurrently using asynchronous HTTP requests.
- Activate the batch after all hospital creations succeed.
- Return per-hospital results and batch processing statistics.
- Optional background processing with live progress tracking via a polling endpoint.
- Retry failed hospital creations and activation.
- Reconcile failed creations against existing batch records before retrying.
- Docker and Docker Compose support.

## Requirements

- Python 3.13+
- Docker (optional, for containerized execution)

## Local Setup

Clone the repository and navigate to the project directory:

```bash
git clone https://github.com/laibakhan83185-hub/hospital-bulk-processor.git
cd hospital-bulk-processor
```

Create and activate a virtual environment:

```bash
python3 -m venv venv
source venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Start the API:

```bash
uvicorn app.main:app --reload
```

Open the interactive API documentation at http://localhost:8000/docs.

## CSV Format

The CSV must contain `name` and `address` columns. The `phone` column is optional.

Example:

```csv
name,address,phone
City Hospital,MG Road,9876543210
County Hospital,Park Street,9876543211
```

A maximum of 20 hospitals is accepted per request. Empty required fields and duplicate hospitals are rejected.

## API Endpoints

### Validate a CSV

`POST /hospitals/bulk/validate`

Validates the uploaded CSV without creating hospitals.

### Create a batch

`POST /hospitals/bulk`

Uploads the CSV, creates hospitals concurrently, and activates the batch if all creations succeed. The request waits until processing finishes and returns the full result.

```bash
curl -F "file=@hospitals.csv" https://hospital-bulk-processor-7hw2.onrender.com/hospitals/bulk
```

Example response:

```json
{
  "batch_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "total_hospitals": 2,
  "processed_hospitals": 2,
  "failed_hospitals": 0,
  "processing_time_seconds": 1.84,
  "batch_activated": true,
  "hospitals": [
    {
      "row": 1,
      "hospital_id": 101,
      "name": "City Hospital",
      "status": "created_and_activated"
    },
    {
      "row": 2,
      "hospital_id": 102,
      "name": "County Hospital",
      "status": "created_and_activated"
    }
  ]
}
```

### Create a batch in the background

`POST /hospitals/bulk?background=true`

Returns immediately with the batch ID and `"status": "processing"`, then processes the hospitals in the background. Use the status endpoint to follow progress.

```bash
curl -F "file=@hospitals.csv" "https://hospital-bulk-processor-7hw2.onrender.com/hospitals/bulk?background=true"
```

### Track progress

`GET /hospitals/bulk/{batch_id}`

Returns the current state of a batch. While a batch is running, `processed_hospitals`, `failed_hospitals`, and the per-hospital results update as each hospital finishes.

The `status` field moves through these stages:

| Status | Meaning |
|---|---|
| `processing` | Hospitals are being created. |
| `activating` | All hospitals were created; the batch is being activated. |
| `completed` | All hospitals were created and the batch is active. |
| `failed` | One or more creations failed, or activation failed. Use the retry endpoint. |

Returns `404` if the batch ID is unknown.

### Retry a batch

`POST /hospitals/bulk/{batch_id}/retry`

Retries failed hospital creations or a previously failed batch activation.

The retry operation checks the external API for existing records in the batch before retrying failed creations to reduce duplicate creation risk.

## Design Decisions

- **Concurrent creation:** All hospitals in a batch are created in parallel with `asyncio.gather`, so total time is close to the slowest single request rather than the sum of all requests.
- **All-or-nothing activation:** The batch is activated only when every hospital was created successfully. Hospitals created with a batch ID stay inactive until then, so a partially failed upload never becomes visible as active data.
- **Live progress:** Batch state is updated after each hospital finishes, so the status endpoint reflects real progress during processing.
- **Safe retries:** Before retrying a failed creation, the service fetches the batch from the external API and skips hospitals that already exist (matched by normalized name and address). This handles the case where a request succeeded remotely but the response was lost.
- **Synchronous by default:** `POST /hospitals/bulk` keeps the response format from the assignment specification. Background mode is opt-in.

## Docker

Build the image:

```bash
docker build -t hospital-bulk-processor .
```

Run the container:

```bash
docker run --rm -p 8000:8000 hospital-bulk-processor
```

Or with Docker Compose:

```bash
docker compose up --build
```

Open http://localhost:8000/docs.

## Run Tests

```bash
python -m pytest -v
```

The tests mock the external Hospital Directory API and cover CSV validation, partial failures, activation failures, retries, duplicate reconciliation, background processing, and live progress updates.

## Architecture

- **FastAPI:** HTTP endpoints, request handling, and background tasks.
- **httpx:** Asynchronous requests to the external Hospital Directory API.
- **asyncio:** Concurrent hospital creation.
- **In-memory batch store:** Stores batch state, progress, and results during the application's lifetime.
- **Docker / Docker Compose:** Packages and runs the application and its dependencies.

## Limitations

- Batch state is stored in memory and is lost when the application restarts. Batches running in the background are also lost if the service restarts mid-processing.
- Retry reconciliation reduces duplicate creation risk but cannot guarantee exactly-once creation if external API results are uncertain.
- Hospital creation and activation depend on the availability and response time of the external Hospital Directory API.

## External API

Hospital Directory API: https://hospital-directory.onrender.com/docs

## License

This project was developed as a programming assignment.
