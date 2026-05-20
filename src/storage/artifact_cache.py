import os
import hashlib
import json
import shutil
import time

class ArtifactCache:
    """
    Manages a local cache for artifacts, including robust checksum validation.

    This cache ensures the integrity of stored artifacts by verifying their content
    against a stored digest before they are reused. Corrupt or incomplete entries
    are automatically evicted.
    """
    CACHE_BASE_DIR = "local_artifact_cache"
    METADATA_FILE_SUFFIX = ".metadata.json"
    DIGEST_ALGORITHM = "sha256" # Standard cryptographic hash algorithm

    def __init__(self, cache_dir: str = None):
        """
        Initializes the ArtifactCache.

        The cache directory is created if it doesn't already exist.

        :param cache_dir: The base directory for storing cached artifacts.
                          Defaults to 'local_artifact_cache'.
        """
        self.cache_dir = cache_dir if cache_dir else self.CACHE_BASE_DIR
        os.makedirs(self.cache_dir, exist_ok=True)

    def _get_artifact_path(self, artifact_id: str) -> str:
        """
        Constructs the full file path for a given artifact ID within the cache.

        :param artifact_id: The unique identifier for the artifact.
        :return: The absolute path where the artifact file should be stored.
        """
        return os.path.join(self.cache_dir, artifact_id)

    def _get_metadata_path(self, artifact_id: str) -> str:
        """
        Constructs the full file path for an artifact's metadata file.

        :param artifact_id: The unique identifier for the artifact.
        :return: The absolute path where the artifact's metadata file should be stored.
        """
        return self._get_artifact_path(artifact_id) + self.METADATA_FILE_SUFFIX

    def _calculate_file_digest(self, file_path: str) -> str:
        """
        Calculates the SHA256 digest of a given file.

        Reads the file in chunks to efficiently handle potentially large files
        without loading the entire content into memory.

        :param file_path: The path to the file.
        :return: The hexadecimal string representation of the file's digest.
        :raises FileNotFoundError: If the specified file does not exist.
        :raises OSError: If there is an issue reading the file.
        """
        hasher = hashlib.new(self.DIGEST_ALGORITHM)
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(4096), b''):
                hasher.update(chunk)
        return hasher.hexdigest()

    def store_artifact(self, artifact_id: str, source_file_path: str, expected_source_digest: str = None) -> str:
        """
        Stores an artifact in the cache, performing optional pre-validation and
        storing the computed digest for future integrity checks.

        This method copies the content from `source_file_path` to the cache location,
        calculates its digest, and saves this digest along with other metadata.

        If `expected_source_digest` is provided, it validates the `source_file_path`
        against this digest *before* caching. If the digests do not match, a ValueError
        is raised, preventing corrupt data from being cached.

        :param artifact_id: A unique identifier for the artifact. This can include
                            directory separators (e.g., 'group/subgroup/artifact_name').
        :param source_file_path: The path to the temporary or downloaded file that needs to be cached.
        :param expected_source_digest: Optional. The expected digest of the `source_file_path`
                                       as provided by the original source (e.g., a manifest).
        :return: The absolute path to the stored artifact within the cache.
        :raises FileNotFoundError: If the `source_file_path` does not exist.
        :raises ValueError: If the source file's digest does not match `expected_source_digest`
                            when `expected_source_digest` is provided, indicating source corruption.
        :raises OSError: If there are issues during file operations (e.g., copy, write).
        """
        dest_artifact_path = self._get_artifact_path(artifact_id)
        metadata_path = self._get_metadata_path(artifact_id)

        if not os.path.exists(source_file_path):
            raise FileNotFoundError(f"Source file to cache not found: {source_file_path}")

        # --- Pre-validation: Check source integrity if an expected digest is provided ---
        if expected_source_digest:
            source_actual_digest = self._calculate_file_digest(source_file_path)
            if source_actual_digest != expected_source_digest:
                raise ValueError(
                    f"Source artifact '{artifact_id}' digest mismatch. "
                    f"Expected: {expected_source_digest}, Got: {source_actual_digest}. "
                    "Aborting cache operation to prevent caching corrupt data."
                )

        # Ensure the target directory for the artifact exists (supports nested artifact_ids)
        os.makedirs(os.path.dirname(dest_artifact_path), exist_ok=True)

        # Copy the file content to the cache location.
        # shutil.copy2 preserves file metadata like timestamps, which can be useful.
        shutil.copy2(source_file_path, dest_artifact_path)

        # Calculate the digest of the *just stored* file. This digest becomes the
        # canonical "expected digest" for all future retrieval attempts from the cache.
        stored_digest = self._calculate_file_digest(dest_artifact_path)

        metadata = {
            "id": artifact_id,
            "path": dest_artifact_path, # Full path for convenience, though derived from ID
            "size": os.path.getsize(dest_artifact_path),
            "digest": stored_digest,
            "algorithm": self.DIGEST_ALGORITHM,
            "cached_at": time.time() # Unix timestamp of successful caching
        }

        # Write metadata file.
        with open(metadata_path, 'w') as f:
            json.dump(metadata, f, indent=4) # Use indent for readability in development/debugging

        return dest_artifact_path

    def retrieve_artifact(self, artifact_id: str) -> str | None:
        """
        Retrieves an artifact from the cache, performing robust checksum validation
        against the stored metadata digest.

        This method first checks for the physical presence of the artifact and its
        metadata. It then loads the metadata and compares the actual content digest
        of the cached file with the stored expected digest. If validation fails
        at any step (e.g., file missing, corrupt metadata, size mismatch, digest mismatch),
        the cache entry is considered corrupt and is invalidated (evicted).

        :param artifact_id: The unique identifier for the artifact.
        :return: The absolute path to the valid cached artifact file, or None if the
                 artifact is not found, its metadata is corrupt, or it fails integrity validation.
        """
        artifact_path = self._get_artifact_path(artifact_id)
        metadata_path = self._get_metadata_path(artifact_id)

        # --- Step 1: Initial check for file and metadata existence ---
        if not os.path.exists(artifact_path) or not os.path.exists(metadata_path):
            # Cache miss or an incomplete entry. No validation needed.
            return None

        # --- Step 2: Load and validate metadata ---
        try:
            with open(metadata_path, 'r') as f:
                metadata = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError, OSError):
            # Metadata is corrupt, unreadable, or file system issue. Invalidate and report miss.
            self._invalidate_cache_entry(artifact_id)
            return None

        # Extract and validate essential metadata fields
        expected_digest = metadata.get("digest")
        stored_algorithm = metadata.get("algorithm")
        stored_size = metadata.get("size")

        if not expected_digest or stored_algorithm != self.DIGEST_ALGORITHM or stored_size is None:
            # Metadata is incomplete, uses an unsupported algorithm, or missing size. Invalidate.
            self._invalidate_cache_entry(artifact_id)
            return None

        # --- Step 3: Validate physical file existence and size against metadata ---
        # This catches partial files or external deletions efficiently without hashing.
        if not os.path.exists(artifact_path) or os.path.getsize(artifact_path) != stored_size:
            # File system might have been tampered with, or file got truncated/deleted.
            self._invalidate_cache_entry(artifact_id)
            return None

        # --- Step 4: Critical checksum validation of the cached file content ---
        try:
            actual_digest = self._calculate_file_digest(artifact_path)
            if actual_digest == expected_digest:
                # Cache hit and content is validated!
                return artifact_path
            else:
                # Checksum mismatch: the stored file content is corrupt. Invalidate and report miss.
                self._invalidate_cache_entry(artifact_id)
                return None
        except (FileNotFoundError, OSError):
            # Edge case: file might have been deleted or become unreadable
            # between previous checks and digest calculation. Invalidate.
            self._invalidate_cache_entry(artifact_id)
            return None
        except Exception:
            # Catch any other unforeseen errors during digest calculation. Invalidate.
            self._invalidate_cache_entry(artifact_id)
            return None

    def _invalidate_cache_entry(self, artifact_id: str):
        """
        Removes the artifact file and its associated metadata file from the cache.
        Handles potential errors during file removal gracefully (e.g., file not found, permissions).

        :param artifact_id: The unique identifier of the artifact to invalidate.
        """
        artifact_path = self._get_artifact_path(artifact_id)
        metadata_path = self._get_metadata_path(artifact_id)

        # Attempt to remove the artifact file
        if os.path.exists(artifact_path):
            try:
                os.remove(artifact_path)
            except OSError:
                # Log this error if a logger is available, but proceed to remove metadata
                pass
        
        # Attempt to remove the metadata file
        if os.path.exists(metadata_path):
            try:
                os.remove(metadata_path)
            except OSError:
                # Log this error if a logger is available
                pass

    def clear_cache(self):
        """
        Removes all artifacts and their metadata from the cache directory.
        Effectively empties the entire cache.
        """
        if os.path.exists(self.cache_dir):
            try:
                shutil.rmtree(self.cache_dir)
                os.makedirs(self.cache_dir) # Recreate the base directory after removal
            except OSError:
                # Log this error if a logger is available
                pass