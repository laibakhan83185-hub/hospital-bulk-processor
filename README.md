# Hospital Bulk Processing API

A FastAPI service that validates CSV files and creates hospitals in bulk through the external Hospital Directory API.

## Features

- Upload a CSV file containing up to 20 hospitals.
- Validate required fields and reject duplicate hospitals.
- Create hospitals concurrently using asynchronous HTTP requests.
- Activate the batch after all hospital creations succeed.
- Return per-hospital results and batch processing statistics.
- Retry failed hospital creations and activation.
- Reconcile failed creations against existing batch records before retrying.
- Docker support.

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

Uploads the CSV, creates hospitals concurrently, and activates the batch if all creations succeed.

The response includes the batch ID, total and processed counts, failed count, processing time, activation status, and per-hospital results.

### Retry a batch

`POST /hospitals/bulk/{batch_id}/retry`

Retries failed hospital creations or a previously failed batch activation.

The retry operation checks the external API for existing records in the batch before retrying failed creations to reduce duplicate creation risk.

## Docker

Build the image:

```bash
docker build -t hospital-bulk-processor .
```

Run the container:

```bash
docker run --rm -p 8000:8000 hospital-bulk-processor
```

Open http://localhost:8000/docs.

## Run Tests

```bash
python -m pytest -v
```

## Architecture

- **FastAPI:** HTTP endpoints and request handling.
- **httpx:** Asynchronous requests to the external Hospital Directory API.
- **asyncio:** Concurrent hospital creation.
- **In-memory batch store:** Stores batch state and results during the application's lifetime.
- **Docker:** Packages the application and its dependencies.

## Limitations

- Batch state is stored in memory and is lost when the application restarts.
- Retry reconciliation reduces duplicate creation risk but cannot guarantee exactly-once creation if external API results are uncertain.
- Hospital creation and activation depend on the availability and response time of the external Hospital Directory API.

## External API

Hospital Directory API: https://hospital-directory.onrender.com/docs

## License

This project was developed as a programming assignment.
