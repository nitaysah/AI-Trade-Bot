# Use a reviewed immutable Python runtime manifest.  The tag documents the
# intended release; the digest prevents a retagged upstream image from
# changing a deployment without a source review.
FROM python:3.13.7-slim-bookworm@sha256:adafcc17694d715c905b4c7bebd96907a1fd5cf183395f0ebc4d3428bd22d92d

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8080
ENV ENV=production

# Set the working directory in the container
WORKDIR /app

# Install system dependencies
ARG DEBIAN_SNAPSHOT=20250901T000000Z
RUN rm -f /etc/apt/sources.list /etc/apt/sources.list.d/debian.sources \
    && printf 'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/%s/ bookworm main\n' "$DEBIAN_SNAPSHOT" > /etc/apt/sources.list \
    && apt-get -o Acquire::Check-Valid-Until=false update \
    && apt-get install -y --no-install-recommends \
    build-essential \
    python3-dev \
    libcurl4-openssl-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy the requirements file into the container
COPY requirements.lock.txt .
COPY webull-requirements.lock.txt .

# Install Python dependencies
RUN python -m pip install --no-cache-dir pip==25.2
RUN pip install --no-cache-dir --require-hashes -r requirements.lock.txt
# Webull SDK metadata caps cryptography below patched versions on Python 3.13.
# Keep it separately hash-locked and install without re-resolving its dependencies.
RUN pip install --no-cache-dir --require-hashes --no-deps -r webull-requirements.lock.txt

# Copy the rest of the backend code into the container
COPY . .

# Expose the port the app runs on
EXPOSE 8080

# Command to run the application using uvicorn
# We use 0.0.0.0 to bind to all interfaces for Cloud Run compatibility
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
