import os
from fastapi import APIRouter, Request, HTTPException, status, Depends
from typing import Annotated, AsyncIterator

# --- Configuration Constants ---
# This constant defines the maximum allowed size for an artifact upload body in bytes.
# For production environments, this value should be loaded from a central
# configuration system (e.g., environment variables, a dedicated settings module)
# to allow for easy adjustments without code changes.
# Using os.getenv to allow easy override via environment variables.
MAX_ARTIFACT_UPLOAD_SIZE_BYTES = int(os.getenv("MAX_ARTIFACT_UPLOAD_SIZE_BYTES", 5 * 1024 * 1024))  # Default: 5 MB

# --- Artifact Service Layer ---
# In a larger application, this would typically reside in a separate file (e.g., `services/artifact_service.py`).
# It encapsulates the business logic for artifact ingestion, including size validation.
class ArtifactService:
    def __init__(self, max_size: int = MAX_ARTIFACT_UPLOAD_SIZE_BYTES):
        self.max_size = max_size

    async def ingest_artifact_stream(self, artifact_stream: AsyncIterator[bytes]) -> int:
        """
        Ingests an artifact from an async byte stream, enforcing a maximum size.
        This method acts as the "guard" within the shared service layer,
        validating inputs before any data is permanently stored or further processed.

        It processes the incoming stream chunk by chunk, checking the cumulative size.
        If the size limit is exceeded, it immediately raises an HTTPException (413),
        failing the request early to prevent resource exhaustion and unnecessary processing.

        In a real-world scenario, instead of merely counting bytes, this is where
        each chunk would be directly streamed to persistent storage (e.g., S3,
        Google Cloud Storage, a local file system) without buffering the entire
        artifact in application memory. This ensures optimal memory utilization
        for large file uploads.

        Args:
            artifact_stream: An asynchronous iterator that yields byte chunks of the artifact body.

        Returns:
            The total size of the ingested artifact in bytes if successful.

        Raises:
            HTTPException:
                - status_code 413 (Request Entity Too Large) if the artifact body size exceeds `self.max_size`.
                - status_code 500 (Internal Server Error) for other unexpected issues during processing or storage.
        """
        body_bytes_read = 0
        
        try:
            async for chunk in artifact_stream:
                body_bytes_read += len(chunk)
                if body_bytes_read > self.max_size:
                    # Raise an exception immediately if the size limit is exceeded.
                    # This implements the "fail closed" principle for oversized inputs.
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"Artifact upload body is too large. "
                               f"Maximum allowed size is {self.max_size / (1024 * 1024):.1f} MB."
                    )
                # --- Placeholder for actual persistent storage operation ---
                # Example:
                # await storage_client.upload_chunk(chunk, artifact_id)
                # await temp_file_writer.write(chunk)
                # -----------------------------------------------------------
            
            # After the loop, if no HTTPException was raised, the entire stream
            # has been consumed and processed within the size limit.
            # Any finalization steps for storage (e.g., closing file, completing S3 multipart upload)
            # would typically occur here.
            
            return body_bytes_read
        except HTTPException:
            # Re-raise HTTPExceptions directly as they are intended error responses.
            raise
        except Exception as e:
            # Catch any other unexpected errors during stream processing or storage logic.
            # In a production system, this error should be logged for debugging.
            # logger.error(f"Error processing artifact stream: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An unexpected error occurred during artifact ingestion."
            )

# --- FastAPI Router Initialization ---
# The prefix ensures all endpoints in this router are mounted under "/artifacts".
# The tag helps categorize API documentation.
router = APIRouter(prefix="/artifacts", tags=["Artifacts"])

# --- Dependencies for injecting services ---
def get_artifact_service_dependency() -> ArtifactService:
    """Provides an instance of the ArtifactService,
    making it available for dependency injection in path operations."""
    return ArtifactService()

# --- API Endpoints ---
@router.post("/")
async def upload_artifact(
    request: Request,
    # Inject the ArtifactService instance into the path operation.
    # This allows the router to delegate artifact ingestion logic to the service layer.
    artifact_service: Annotated[ArtifactService, Depends(get_artifact_service_dependency)]
):
    """
    Handles the upload of an artifact.

    The request body, containing the artifact's content, is streamed directly
    to the `artifact_service`. The service layer is responsible for
    enforcing size limits, processing the stream chunk by chunk, and
    persisting the artifact content to storage. This approach optimizes
    memory usage by avoiding full in-memory buffering for large uploads
    and centralizes validation logic as required by the bug report.
    """
    # The artifact_service.ingest_artifact_stream method contains the primary
    # "guard" logic for size validation, ensuring inputs are validated
    # before any mutations or lookups occur.
    size_bytes = await artifact_service.ingest_artifact_stream(request.stream())

    # If the service call completes without raising an HTTPException,
    # the artifact has been successfully validated for size and processed
    # (or initiated for processing/storage).
    return {
        "message": "Artifact ingestion initiated successfully.",
        "size_bytes": size_bytes,
        "status": "validated_and_accepted_for_processing"
    }

# Example of another endpoint (e.g., for retrieving artifacts) to illustrate
# typical router structure and maintain consistent coding style.
@router.get("/{artifact_id}")
async def get_artifact(artifact_id: str):
    """
    Retrieves an artifact by its unique identifier.

    (Placeholder - actual implementation would typically fetch the artifact
    from storage via a service layer.)
    """
    # Example: artifact_content = await artifact_service.retrieve_artifact(artifact_id)
    # if not artifact_content:
    #     raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found.")
    # return StreamingResponse(io.BytesIO(artifact_content), media_type="application/octet-stream")

    return {
        "message": f"Artifact {artifact_id} retrieval placeholder.",
        "artifact_id": artifact_id,
        "detail": "Implementation for retrieval would go here."
    }