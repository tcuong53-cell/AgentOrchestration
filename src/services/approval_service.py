from typing import Optional, Dict, Any
from enum import Enum
import asyncio # Required for simulating async I/O in the mock repository
from fastapi import HTTPException, status

# Define enums for RunState and RunStepStatus for improved type safety and clarity.
class RunState(str, Enum):
    """Defines the possible states for a run."""
    PENDING = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

class RunStepStatus(str, Enum):
    """Defines the possible step statuses for a run."""
    WAITING_HUMAN_APPROVAL = "waiting_human_approval"
    HUMAN_APPROVED = "human_approved"
    HUMAN_REJECTED = "human_rejected"
    PROCESSED = "processed"
    SYSTEM_ERROR = "system_error"

class RunRepository:
    """
    A mock repository for run data.
    In a real application, this would interact with a database (e.g., SQLAlchemy, MongoDB).
    """
    _db: Dict[str, Dict[str, Any]] = {
        "run_123": {"state": RunState.PENDING, "step_status": RunStepStatus.WAITING_HUMAN_APPROVAL, "metadata": {}},
        "run_456": {"state": RunState.COMPLETED, "step_status": RunStepStatus.PROCESSED, "metadata": {}},
        "run_789": {"state": RunState.PENDING, "step_status": RunStepStatus.WAITING_HUMAN_APPROVAL, "metadata": {}},
        "run_000": {"state": RunState.FAILED, "step_status": RunStepStatus.SYSTEM_ERROR, "metadata": {}},
        "run_already_approved": {"state": RunState.APPROVED, "step_status": RunStepStatus.HUMAN_APPROVED, "metadata": {}},
        "run_already_rejected": {"state": RunState.REJECTED, "step_status": RunStepStatus.HUMAN_REJECTED, "metadata": {}},
    }

    async def get_run_by_id(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a run by its ID from the mock database."""
        # Simulate async database call
        await asyncio.sleep(0.01)
        return self._db.get(run_id)

    async def update_run_state(self, run_id: str, new_state: RunState, step_status: RunStepStatus) -> bool:
        """Updates the state and step status of a run in the mock database."""
        # Simulate async database call
        await asyncio.sleep(0.01)
        if run_id in self._db:
            self._db[run_id]["state"] = new_state
            self._db[run_id]["step_status"] = step_status
            return True
        return False

# Instantiate the repository. In a real FastAPI application, this would typically
# be injected as a dependency (e.g., using `Depends` in router functions).
run_repository = RunRepository()

async def approve_human_step(run_id: str, approved: bool, reason: Optional[str] = None) -> RunState:
    """
    Approves or rejects a human step for a given run.

    This function includes a critical "guard" to ensure that an approval/rejection
    can only proceed if the run is in a 'pending_approval' state and specifically
    'waiting_human_approval' for its step status. This prevents invalid state transitions
    or processing of already completed/failed runs.

    Args:
        run_id (str): The ID of the run to approve or reject.
        approved (bool): True if approved, False if rejected.
        reason (Optional[str]): An optional reason for the approval or rejection.

    Returns:
        RunState: The new state of the run (RunState.APPROVED or RunState.REJECTED).

    Raises:
        HTTPException: If the run is not found, or if it's not in an
                       approvable state, or if the update fails.
    """
    run = await run_repository.get_run_by_id(run_id)

    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run with ID '{run_id}' not found."
        )

    # FIX: Implement the "guard" here at the start of the logic,
    # after validating the run exists. This prevents processing requests for runs that
    # are already completed, failed, or not genuinely awaiting human approval.
    # The validation fails closed early, before any mutation.
    if run["state"] != RunState.PENDING or run["step_status"] != RunStepStatus.WAITING_HUMAN_APPROVAL:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Run '{run_id}' is not in a state that can be approved. "
                f"Current state: '{run['state']}', Step status: '{run['step_status']}'. "
                f"Expected state: '{RunState.PENDING.value}', Expected step status: '{RunStepStatus.WAITING_HUMAN_APPROVAL.value}'."
            )
        )

    if approved:
        new_state = RunState.APPROVED
        step_status = RunStepStatus.HUMAN_APPROVED
    else:
        new_state = RunState.REJECTED
        step_status = RunStepStatus.HUMAN_REJECTED

    # Attempt to update the run state in the repository.
    success = await run_repository.update_run_state(run_id, new_state, step_status)

    if not success:
        # This error indicates a deeper issue, potentially a race condition where the run
        # disappeared or became un-updatable after the initial lookup and guard check.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update state for run '{run_id}'. Please try again."
        )

    # In a real application, further actions would typically be dispatched here,
    # such as sending notifications, triggering the next automated step in a workflow,
    # or publishing an event to a message queue.
    # Example:
    # await event_publisher.publish_run_event(
    #     event_type="human_step_completed",
    #     payload={"run_id": run_id, "new_state": new_state, "approved": approved, "reason": reason}
    # )

    return new_state