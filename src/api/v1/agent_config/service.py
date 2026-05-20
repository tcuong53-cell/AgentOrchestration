from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from fastapi import HTTPException, status
import asyncio

# --- Pydantic Models ---
class AgentConfigUpdateItem(BaseModel):
    """
    Schema for a single agent configuration item to be updated.
    """
    agent_id: str = Field(..., description="Unique ID of the agent whose configuration is being updated.")
    new_status: str = Field(..., description="The desired new status for the agent config (e.g., 'ACTIVE', 'INACTIVE', 'PENDING_ACTIVATION', 'DECOMMISSIONED').")
    # Add other fields relevant to agent config updates
    config_setting_a: Optional[str] = Field(None, description="An example configuration setting.")
    config_setting_b: Optional[int] = Field(None, description="Another example configuration setting.")

class AgentConfigUpdateResponse(BaseModel):
    """
    Schema for the response of a single agent configuration update.
    """
    agent_id: str = Field(..., description="The ID of the agent that was targeted for update.")
    success: bool = Field(..., description="True if the update for this agent was successful, False otherwise.")
    message: Optional[str] = Field(None, description="A message providing more details about the update status (e.g., error message).")

# --- Custom Exception ---
class BatchUpdateValidationError(HTTPException):
    """
    Custom exception for handling batch validation failures.
    It carries a list of errors, each detailing an issue with a specific item.
    """
    def __init__(self, detail: List[Dict[str, Any]]):
        super().__init__(status_code=status.HTTP_400_BAD_REQUEST, detail={"errors": detail})

# --- Service Layer ---
class AgentConfigService:
    """
    Service layer for managing agent configurations.
    Handles business logic and data persistence (simulated here).
    """
    def __init__(self):
        # Initialize dependencies here, e.g., database session, external API clients
        pass

    async def _validate_single_config_business_rules(self, config_item: AgentConfigUpdateItem) -> Optional[Dict[str, Any]]:
        """
        Performs business logic validation for a single agent configuration item.
        This guard checks rules that go beyond Pydantic's structural validation
        (e.g., existence checks, state transition rules, permissions).

        Returns:
            A dictionary containing agent_id and specific errors if validation fails,
            otherwise None.
        """
        errors = {}

        # --- Example Business Rules Validation ---

        # Rule 1: Check if agent_id actually exists in the system.
        # In a real application, this would involve a database query or a call to another service.
        # This is an async operation, so running it concurrently in the batch is beneficial.
        # For demonstration, let's say 'nonexistent_agent_123' is an invalid ID.
        if config_item.agent_id == "nonexistent_agent_123":
            errors["agent_id"] = f"Agent with ID '{config_item.agent_id}' does not exist."
            # Simulate a brief async I/O operation for existence check
            await asyncio.sleep(0.01)

        # Rule 2: Validate `new_status` against a predefined set of valid statuses.
        valid_statuses = {"ACTIVE", "INACTIVE", "PENDING_ACTIVATION", "DECOMMISSIONED"}
        if config_item.new_status not in valid_statuses:
            errors["new_status"] = (
                f"Invalid status '{config_item.new_status}' for agent '{config_item.agent_id}'. "
                f"Must be one of {list(valid_statuses)}."
            )

        # Rule 3: Example of a more complex state transition rule.
        # Assume we fetch the current status of the agent. This would also be an async call.
        # current_status = await self._get_current_agent_status(config_item.agent_id)
        # For demonstration, let's hardcode an example:
        if config_item.agent_id == "agent_critical_456" and config_item.new_status == "DECOMMISSIONED":
             # Simulate a critical agent that cannot be directly decommissioned without approval
            errors["status_transition"] = (
                f"Agent '{config_item.agent_id}' is in a critical state and cannot be "
                f"directly set to 'DECOMMISSIONED'. Requires manual approval or intermediate steps."
            )
            # Simulate a brief async I/O operation for state transition check
            await asyncio.sleep(0.01)
        
        # Rule 4: Validate custom config settings if they exist
        if config_item.config_setting_a is not None and len(config_item.config_setting_a) > 50:
            errors["config_setting_a"] = "config_setting_a must be 50 characters or less."
        if config_item.config_setting_b is not None and config_item.config_setting_b < 0:
            errors["config_setting_b"] = "config_setting_b cannot be negative."

        if errors:
            return {"agent_id": config_item.agent_id, "errors": errors}
        return None

    async def update_agent_configs_batch(self, configs: List[AgentConfigUpdateItem]) -> List[AgentConfigUpdateResponse]:
        """
        Updates a batch of agent configurations.

        This method implements the "fail-closed" principle for batch updates:
        It performs comprehensive business logic validation across all items *before*
        attempting any state mutations (e.g., database writes). If *any* item
        in the batch fails validation, the entire operation is rejected, and
        a detailed error response is returned, preventing partial updates
        and masking of failures due to invalid input.

        Args:
            configs: A list of AgentConfigUpdateItem objects, each representing
                     an agent configuration to be updated.

        Returns:
            A list of AgentConfigUpdateResponse objects detailing the outcome
            of each individual update, if all items passed initial validation.
            Note: If any individual item fails *at runtime* during the update
            process (after passing initial validation), its failure will be
            reported in its respective AgentConfigUpdateResponse.

        Raises:
            BatchUpdateValidationError: If any item in the input batch fails
                                        business logic validation, the entire
                                        batch operation is aborted with a 400
                                        Bad Request, and no state changes occur.
            HTTPException (500): For unexpected internal server errors during
                                 the actual update process after validation,
                                 if a full rollback strategy for *all* failures
                                 (including runtime) is strictly adopted.
        """
        # --- PHASE 1: PRE-UPDATE BUSINESS LOGIC VALIDATION (Fail-Closed Guard) ---
        # This is the critical guard to prevent invalid input from proceeding.
        # Run validations concurrently for better performance, especially if
        # _validate_single_config_business_rules involves I/O operations (e.g., DB lookups).
        validation_tasks = [
            self._validate_single_config_business_rules(config_item)
            for config_item in configs
        ]
        
        # Await all validation tasks concurrently.
        # _validate_single_config_business_rules returns a dict (errors) or None,
        # so asyncio.gather will collect these results.
        raw_validation_results = await asyncio.gather(*validation_tasks)
        
        # Filter out successful validations (where result is None) to get only errors.
        validation_errors = [result for result in raw_validation_results if result is not None]
        
        # If any validation errors were found, raise the custom exception immediately.
        # This ensures that no state changes occur if the input is fundamentally invalid.
        if validation_errors:
            raise BatchUpdateValidationError(detail=validation_errors)

        # --- PHASE 2: EXECUTE BATCH UPDATES (if all items passed validation) ---
        # If we reach this point, all items in the batch are deemed valid by
        # business rules, and we can proceed with actual updates.
        #
        # In a real application, this entire phase should ideally be wrapped in
        # a database transaction to ensure atomicity for persistent storage (all or nothing).
        # For example, using a context manager: `async with self.db_session.begin():`
        # If full atomicity (rollback entire batch on *any* runtime error) is strictly required:
        #   try:
        #       # Perform actual update logic for all items
        #       # If any operation fails, the exception would propagate,
        #       # causing the transaction to roll back implicitly (if using context manager).
        #       # For example: await self._perform_atomic_db_update_batch(configs)
        #       # If successful, return all items as successful
        #       return [AgentConfigUpdateResponse(agent_id=item.agent_id, success=True, message="Updated successfully.") for item in configs]
        #   except Exception as e:
        #       # Rollback handled by transaction manager, raise a 500 for the client
        #       raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Batch update failed due to internal error: {str(e)}")
        #
        # The current implementation reports individual item successes/failures
        # for runtime errors after initial validation, aligning with the
        # `AgentConfigUpdateResponse` model's implied behavior.
        update_responses = []
        for config_item in configs:
            try:
                # --- Simulate the actual update operation ---
                # In a real application, this would involve:
                # 1. Fetching the existing agent record from DB.
                # 2. Applying the updates (e.g., `agent.status = config_item.new_status`).
                # 3. Committing changes to the database for this specific item,
                #    or preparing for a bulk commit later.
                #    (Ideally, this commit would be part of a larger, atomic transaction.)
                print(f"Simulating update for agent_id: {config_item.agent_id}, new_status: {config_item.new_status}")
                await asyncio.sleep(0.05) # Simulate DB write latency

                if config_item.agent_id == "agent_db_error_789":
                    # Simulate a runtime failure that might occur *after* validation
                    # (e.g., database constraint violation, race condition, network issue during commit)
                    raise ValueError("Simulated database connection error during update.")

                update_responses.append(
                    AgentConfigUpdateResponse(
                        agent_id=config_item.agent_id,
                        success=True,
                        message="Configuration updated successfully."
                    )
                )
            except Exception as e:
                # For runtime failures of *valid* inputs, report them individually.
                # This strategy means that if one valid item fails at runtime, others
                # that succeed will still be reported as such. This is a "partial success"
                # in terms of runtime execution but not in terms of invalid input being masked
                # (since the failure is explicitly reported).
                update_responses.append(
                    AgentConfigUpdateResponse(
                        agent_id=config_item.agent_id,
                        success=False,
                        message=f"Failed to update config: {str(e)}"
                    )
                )

        return update_responses