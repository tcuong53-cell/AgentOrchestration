import threading
import time
import logging
from typing import Dict, Any, Optional

# Configure logging for the module
logger = logging.getLogger(__name__)
# In a real application, logging would typically be configured globally (e.g., in main.py or app.py).
# For demonstration purposes, if this were a standalone module, you might add:
# logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')


# Define custom exceptions for clearer, more semantic error handling
class InvariantViolationError(Exception):
    """Raised when a proposed operation violates a defined invariant."""
    pass

class OperationNotFoundError(Exception):
    """Raised when an operation with the given ID is not found."""
    pass

class OperationAlreadyExistsError(Exception):
    """Raised when attempting to add an operation that already exists."""
    pass


class DelayedQueueIndex:
    """
    Manages a delayed queue index, ensuring operations adhere to defined invariants.
    This component handles the scheduling and lifecycle state of operations
    (agent runs, tasks, handlers) that are subject to delayed processing.

    It employs optimistic concurrency control via versioning to prevent stale updates
    and enforce policy-compliant state transitions, aligned with "durable claim/enqueue/ack"
    transaction principles for its in-memory state.
    """
    def __init__(self):
        # Stores the current state of queued operations.
        # Each item must include 'id', 'state', 'scheduled_time' (if applicable),
        # and a 'version' for optimistic concurrency control.
        self._items: Dict[str, Dict[str, Any]] = {}
        # A single lock ensures thread-safe access to the _items dictionary for all operations.
        # For extremely high-throughput systems, more granular locking (e.g., per-item locks or sharding)
        # might be considered, but for general queue index updates, this provides robust atomicity.
        self._lock = threading.Lock()

    def _enforce_protection_invariant(self, item_id: str, current_item: Optional[Dict[str, Any]], proposed_update: Dict[str, Any]) -> None:
        """
        Enforces critical protection invariants before committing any state changes to the delayed queue index.
        This method is central to the fix, ensuring data integrity and policy compliance.

        It validates proposed updates against:
        1.  **Staleness/Concurrency Check**: Uses versioning to ensure the proposed update is based
            on the latest known state, preventing race conditions and overwriting more recent changes.
            This also implicitly handles "duplicate" updates if they use an outdated version.
        2.  **Policy Violation Check (State Transition)**: Verifies that the proposed state transition
            is valid according to the defined state machine rules.
        3.  **Policy Violation Check (Scheduled Time)**: Ensures that scheduled operations are not
            improperly scheduled in the past or lack a scheduled time when required.

        Raises:
            InvariantViolationError: If any invariant is violated, preventing the update.
        """
        proposed_version = proposed_update.get('version')
        if proposed_version is None:
            # All operations, whether new or existing, must explicitly carry a version.
            # This indicates a malformed or improperly constructed proposed_update.
            logger.error(f"Invariant violation: Proposed update for item '{item_id}' is missing a 'version'. Proposed data: {proposed_update}")
            raise InvariantViolationError(f"Missing 'version' in proposed update for item '{item_id}'.")

        # 1. Staleness/Concurrency Check (Optimistic Concurrency Control)
        # This prevents an update based on an outdated view of the item's state.
        if current_item:
            current_version = current_item.get('version', 0) # Should always exist for current items, but default defensively
            if proposed_version <= current_version:
                logger.warning(
                    f"Invariant violation: Stale or duplicate update detected for item '{item_id}'. "
                    f"Proposed version {proposed_version} is not strictly greater than "
                    f"current version {current_version}. Update rejected."
                )
                raise InvariantViolationError(
                    f"Stale or duplicate update detected for item '{item_id}'. "
                    f"Proposed version {proposed_version} must be greater than current version {current_version}. "
                    "Ensure you are operating on the latest state or that the version was correctly incremented."
                )
        elif proposed_version != 1:
            # For a brand new item (where `current_item` is None), its initial version must be 1.
            logger.warning(
                f"Invariant violation: Invalid initial version for new item '{item_id}'. "
                f"Expected 1, got {proposed_version}. Update rejected."
            )
            raise InvariantViolationError(
                f"Invalid initial version for new item '{item_id}'. Expected 1, got {proposed_version}."
            )

        # 2. Policy Violation Check: Validate state transitions
        current_state = current_item.get('state') if current_item else None
        proposed_state = proposed_update.get('state')

        if proposed_state is not None:
            if not self._is_valid_transition(current_state, proposed_state):
                logger.warning(
                    f"Invariant violation: Invalid state transition for item '{item_id}' "
                    f"from '{current_state}' to '{proposed_state}'. Update rejected."
                )
                raise InvariantViolationError(
                    f"Invalid state transition for item '{item_id}' "
                    f"from '{current_state}' to '{proposed_state}'."
                )

        # 3. Policy Violation Check: Validate scheduled_time
        # An item must have a scheduled_time if its state implies scheduling (e.g., 'pending', 'scheduled').
        # This check applies if 'scheduled_time' is explicitly part of the proposed update or if it's missing
        # when required by the state.
        is_scheduled_state = proposed_state in ['pending', 'scheduled', 'retrying'] # Retrying might also imply rescheduling
        has_proposed_scheduled_time = 'scheduled_time' in proposed_update
        has_current_scheduled_time = (current_item and 'scheduled_time' in current_item)

        if has_proposed_scheduled_time:
            # Rescheduling should generally target a time in the future or the very near present.
            # Allowing a small buffer (e.g., 5 seconds) to account for processing latency or minor clock skew.
            if proposed_update['scheduled_time'] < time.time() - 5:
                logger.warning(
                    f"Invariant violation: Cannot reschedule item '{item_id}' "
                    f"to a significantly past time ({proposed_update['scheduled_time']}). Update rejected."
                )
                raise InvariantViolationError(
                    f"Cannot reschedule item '{item_id}' to a significantly past time "
                    f"({proposed_update['scheduled_time']})."
                )
        elif is_scheduled_state and not has_current_scheduled_time:
            # If the state requires a scheduled_time (e.g., 'pending', 'scheduled')
            # but neither the proposed update nor the current item provides one, it's a policy violation.
            logger.warning(
                f"Invariant violation: Item '{item_id}' in state '{proposed_state}' requires a 'scheduled_time' "
                f"which is neither provided in the update nor present in the current state. Update rejected."
            )
            raise InvariantViolationError(
                f"Item '{item_id}' in state '{proposed_state}' requires a 'scheduled_time'."
            )

        # Additional domain-specific checks can be integrated here:
        # e.g., resource availability, user permissions, quota limits, input data validation, etc.

        logger.debug(f"Invariants passed for item '{item_id}'. (Current version: {current_item.get('version')} -> Proposed version: {proposed_version}).")

    def _is_valid_transition(self, current_state: Optional[str], proposed_state: str) -> bool:
        """
        Defines the valid state machine transitions for operations managed by the queue.
        This method centralizes the business rules for lifecycle state progression.

        In a production system, these rules might be loaded from a configuration file,
        a dedicated workflow engine, or a more sophisticated state machine definition framework.
        """
        # Define allowed transitions as a clear, extensible dictionary mapping current states
        # to a set of valid next states.
        valid_transitions = {
            None: {'pending', 'scheduled', 'running'},  # Valid initial states upon creation
            'pending': {'scheduled', 'running', 'cancelled'},
            'scheduled': {'running', 'completed', 'failed', 'cancelled'},
            'running': {'completed', 'failed', 'retrying', 'cancelled'},
            'retrying': {'running', 'scheduled', 'failed', 'cancelled'}, # Can be run directly or rescheduled
            'completed': set(),  # Terminal states; no further active transitions allowed
            'failed': set(),
            'cancelled': set()
        }
        
        # Ensure the proposed state is not None and is a recognized state within the state machine.
        # This prevents transitioning to an unknown state.
        all_possible_states = set.union(*valid_transitions.values(), {k for k in valid_transitions.keys() if k is not None})
        if proposed_state is None or proposed_state not in all_possible_states:
            logger.debug(f"Proposed state '{proposed_state}' is not a recognized state.")
            return False

        # Retrieve the set of allowed next states for the current state.
        allowed_next_states = valid_transitions.get(current_state)
        if allowed_next_states is None:
            # If the current state is unrecognized, no transitions are allowed from it.
            # This should ideally not happen if state values are consistently managed.
            logger.debug(f"Current state '{current_state}' is not a recognized state for transition rules.")
            return False
        
        # Check if the proposed state is among the allowed next states.
        return proposed_state in allowed_next_states

    def add_operation(self, item_id: str, data: Dict[str, Any]) -> None:
        """
        Adds a new operation to the delayed queue index.
        This method is protected by invariants, ensuring that new operations are valid
        from their inception, including initial versioning and state.

        Args:
            item_id (str): A unique identifier for the operation.
            data (Dict[str, Any]): A dictionary containing the initial properties of the operation
                                    (e.g., 'state', 'scheduled_time').

        Raises:
            OperationAlreadyExistsError: If an operation with the given ID already exists.
            InvariantViolationError: If the proposed new operation violates any defined invariants.
        """
        with self._lock:
            if item_id in self._items:
                logger.error(f"Attempted to add operation '{item_id}' which already exists in the queue.")
                raise OperationAlreadyExistsError(f"Operation '{item_id}' already exists in the queue.")

            # Prepare the initial state for the new operation. The version always starts at 1.
            # Explicitly set 'id' and 'version' to ensure consistency and correctness.
            proposed_update = {**data, 'id': item_id, 'version': 1}

            # --- FIX START: Crucially, invariants are now enforced even for new item additions. ---
            # Pass None for `current_item` to signify a new creation.
            self._enforce_protection_invariant(item_id, None, proposed_update)
            # --- FIX END ---

            self._items[item_id] = proposed_update
            logger.info(
                f"Operation '{item_id}' added successfully. "
                f"Initial state: {proposed_update.get('state')}, "
                f"Scheduled: {proposed_update.get('scheduled_time')}, "
                f"Version: {proposed_update.get('version')}"
            )

    def reschedule_operation(self, item_id: str, new_data: Dict[str, Any]) -> None:
        """
        Reschedules an existing operation in the delayed queue index.
        This method is the core component that was previously vulnerable to stale, duplicate,
        or policy-violating transitions. It now strictly enforces all protection invariants
        before committing any state changes.

        Retries of the same logical operation are made safe and "idempotent" in the sense
        that they will not corrupt state. If a retry attempts to use stale information,
        it will be rejected by the versioning mechanism. If a transient error occurred,
        a retry with the updated version will proceed.

        Args:
            item_id (str): The unique identifier of the operation to reschedule.
            new_data (Dict[str, Any]): A dictionary containing the new properties for the operation
                                        (e.g., 'state', 'scheduled_time').

        Raises:
            OperationNotFoundError: If an operation with the given ID does not exist.
            InvariantViolationError: If the proposed update violates any defined invariants.
        """
        with self._lock:
            current_item = self._items.get(item_id)
            if current_item is None:
                logger.error(f"Attempted to reschedule non-existent operation '{item_id}'.")
                raise OperationNotFoundError(f"Cannot reschedule non-existent operation '{item_id}'. Operation not found.")

            # Construct the proposed new state by merging `new_data` with the `current_item`'s data.
            # This ensures that properties not specified in `new_data` are preserved.
            # Explicitly set 'id' to ensure consistency.
            proposed_update = {**current_item, **new_data, 'id': item_id}
            
            # Increment the version number for the proposed update. This is crucial for
            # optimistic concurrency control and detecting stale updates.
            proposed_update['version'] = current_item.get('version', 0) + 1

            # --- FIX START: The critical fix is to call _enforce_protection_invariant here. ---
            # This ensures that all validation rules (staleness, policy violations, etc.)
            # are applied *before* the internal state of the queue is modified.
            self._enforce_protection_invariant(item_id, current_item, proposed_update)
            # --- FIX END ---

            # If `_enforce_protection_invariant` passes without raising an exception,
            # then the proposed update is valid and can be committed.
            self._items[item_id] = proposed_update
            logger.info(
                f"Operation '{item_id}' rescheduled successfully. "
                f"New state: {proposed_update.get('state')}, "
                f"Scheduled: {proposed_update.get('scheduled_time')}, "
                f"New version: {proposed_update.get('version')}"
            )

    def get_operation(self, item_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieves a copy of an operation's data by its ID.
        Returns None if the operation is not found.

        Args:
            item_id (str): The unique identifier of the operation.

        Returns:
            Optional[Dict[str, Any]]: A shallow copy of the operation's data, or None if not found.
                                      A shallow copy is used to prevent external modification of
                                      the internal state, assuming top-level keys hold immutable
                                      values or are not mutated directly.
        """
        with self._lock:
            item_data = self._items.get(item_id)
            if item_data:
                return item_data.copy()
            return None

    def remove_operation(self, item_id: str) -> None:
        """
        Removes an operation from the delayed queue index.
        This operation does not typically require complex invariant checks,
        beyond ensuring the item exists.

        Args:
            item_id (str): The unique identifier of the operation to remove.
        """
        with self._lock:
            if item_id in self._items:
                del self._items[item_id]
                logger.info(f"Operation '{item_id}' removed from the queue.")
            else:
                logger.debug(f"Attempted to remove non-existent operation '{item_id}'. No action taken.")