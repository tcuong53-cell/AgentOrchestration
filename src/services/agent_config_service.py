from typing import Optional
from fastapi import HTTPException, status
from pydantic import BaseModel, Field
import uuid

# --- Agent Configuration Models ---
# These models define the structure of the agent configuration data.
# In a larger project, these might reside in a separate `src/models/agent_config.py` file.

class AgentConfig(BaseModel):
    """
    Represents an agent configuration, including its version for optimistic locking.
    The 'version' field serves as the ETag.
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Unique ID of the agent configuration")
    name: str = Field(..., description="Name of the agent configuration")
    config_data: dict = Field(..., description="The actual configuration data for the agent")
    version: int = Field(0, description="Version for optimistic locking (ETag). Incremented on every successful update.")

class AgentConfigUpdate(BaseModel):
    """
    Model for updating an existing agent configuration.
    Fields are optional to allow partial updates.
    """
    name: Optional[str] = Field(None, description="New name for the agent configuration")
    config_data: Optional[dict] = Field(None, description="New configuration data for the agent")

# --- Mock Database ---
# In a real application, this would be a persistent database (e.g., PostgreSQL, MongoDB).
# This dictionary simulates in-memory storage for demonstration purposes.
_agent_configs_db: dict[str, AgentConfig] = {}

# --- Agent Configuration Service ---
# This service layer handles the business logic for managing agent configurations,
# including the optimistic locking mechanism to prevent stale data overwrites.

class AgentConfigService:
    """
    Service layer for managing agent configurations with optimistic locking.
    Ensures data consistency and prevents lost updates through ETag-based concurrency control.
    """

    def get_agent_config(self, config_id: str) -> Optional[AgentConfig]:
        """
        Retrieves an agent configuration by its unique ID.
        """
        return _agent_configs_db.get(config_id)

    def create_agent_config(self, config_data: AgentConfig) -> AgentConfig:
        """
        Creates a new agent configuration.
        Assigns an initial version for optimistic locking.

        Raises:
            HTTPException: 409_CONFLICT if an agent config with the given ID already exists.
        """
        if self.get_agent_config(config_data.id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Agent config with ID '{config_data.id}' already exists."
            )
        config_data.version = 1  # Initial version for a new resource
        _agent_configs_db[config_data.id] = config_data
        return config_data

    def update_agent_config(self, config_id: str, update_data: AgentConfigUpdate, expected_etag: Optional[str]) -> AgentConfig:
        """
        Updates an existing agent configuration, strictly enforcing optimistic locking.

        This method validates the `expected_etag` against the current resource's version
        *before* attempting any mutation. This prevents stale data from overwriting newer state.

        Args:
            config_id: The ID of the agent configuration to update.
            update_data: The data to apply to the configuration.
            expected_etag: The ETag (version) provided by the client,
                           typically from an `If-Match` HTTP header. This is crucial
                           for optimistic concurrency control.

        Returns:
            The updated AgentConfig object.

        Raises:
            HTTPException:
                - 404_NOT_FOUND if the configuration does not exist.
                - 428_PRECONDITION_REQUIRED if `expected_etag` is missing, enforcing optimistic locking.
                - 400_BAD_REQUEST if the `expected_etag` format is invalid (not an integer string).
                - 412_PRECONDITION_FAILED if the `expected_etag` does not match
                  the current version, indicating a stale client state.
        """
        # Validate inputs early, as per acceptance criteria.
        # Ensure an ETag is provided to prevent non-optimistic updates.
        if not expected_etag:
            raise HTTPException(
                status_code=status.HTTP_428_PRECONDITION_REQUIRED,
                detail="If-Match header (expected ETag) is required for optimistic updates "
                       "to prevent stale overwrites. Provide the ETag of the version you intend to update."
            )

        try:
            # The ETag is expected to be the string representation of the integer version.
            expected_version = int(expected_etag)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid ETag format. ETag must be an integer string representing the resource version (e.g., '1', '2')."
            )

        existing_config = self.get_agent_config(config_id)

        if not existing_config:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Agent config with ID '{config_id}' not found."
            )

        # Core optimistic locking guard: Compare the client's expected version with the current stored version.
        if existing_config.version != expected_version:
            # If they do not match, the client's data is stale, and the update is rejected.
            # This prevents overwriting newer state and ensures ordered updates.
            raise HTTPException(
                status_code=status.HTTP_412_PRECONDITION_FAILED,
                detail=f"Precondition Failed: ETag mismatch. The resource has been updated by another process. "
                       f"Client's ETag was '{expected_etag}', but the current ETag is '{existing_config.version}'. "
                       f"Please fetch the latest version and retry."
            )

        # Apply updates from the request data only if the ETag precondition is met.
        # `exclude_unset=True` ensures only explicitly provided fields are updated.
        update_fields = update_data.model_dump(exclude_unset=True)
        for key, value in update_fields.items():
            setattr(existing_config, key, value)

        # Increment the version after a successful update.
        # This new version will be the ETag for subsequent updates.
        existing_config.version += 1

        # In a real system, this would be a database commit.
        _agent_configs_db[config_id] = existing_config
        return existing_config

    def delete_agent_config(self, config_id: str) -> None:
        """
        Deletes an agent configuration by ID.

        Raises:
            HTTPException: 404_NOT_FOUND if the configuration does not exist.
        """
        if config_id not in _agent_configs_db:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Agent config with ID '{config_id}' not found."
            )
        del _agent_configs_db[config_id]