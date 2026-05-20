import logging
from enum import Enum, auto
from typing import Optional, Dict, Any
import time

logger = logging.getLogger(__name__)

# --- Mock Data Structures (replace with actual ORM models/DB interaction in a real system) ---
# These classes simulate the entities managed by the orchestrator and the dispatch queue.
class LifecycleState(Enum):
    """Represents the lifecycle state of an agent run, task, or handler."""
    PENDING = auto()
    SCHEDULED = auto()
    RUNNING = auto()
    COMPLETED = auto()
    FAILED = auto()
    CANCELLED = auto()
    DISPATCH_FAILED = auto() # A specific state for failed dispatch attempts

class DispatchTargetType(Enum):
    """Defines the type of entity a dispatch targets."""
    AGENT_RUN = auto()
    TASK = auto()
    HANDLER = auto()

class EntityState:
    """
    Represents the current state of a managed entity (e.g., Task, AgentRun).
    Includes a version for optimistic locking.
    """
    def __init__(self, entity_id: str, entity_type: DispatchTargetType, lifecycle_state: LifecycleState, version: int = 0, dispatch_id: Optional[str] = None):
        self.entity_id = entity_id
        self.entity_type = entity_type
        self.lifecycle_state = lifecycle_state
        self.version = version # Used for optimistic locking to detect concurrent modifications
        self.dispatch_id = dispatch_id # ID of the dispatch record that last affected this entity

    def __repr__(self):
        return f"EntityState(id={self.entity_id}, type={self.entity_type.name}, state={self.lifecycle_state.name}, ver={self.version}, dispatch_id={self.dispatch_id})"

class DispatchQueueItem:
    """
    Represents an item in the dispatch queue, indicating an intent to dispatch an entity.
    """
    def __init__(self, item_id: str, target_entity_id: str, target_entity_type: DispatchTargetType, payload: Dict[str, Any]):
        self.item_id = item_id
        self.target_entity_id = target_entity_id
        self.target_entity_type = target_entity_type
        self.payload = payload
        self.status = "QUEUED" # "QUEUED", "PROCESSING", "COMPLETED", "FAILED", "SKIPPED_POLICY_VIOLATION", "FAILED_CONCURRENCY_CONFLICT", "SATISFIED_ALREADY_ACTIVE"

    def __repr__(self):
        return f"DispatchQueueItem(id={self.item_id}, target={self.target_entity_id}, type={self.target_entity_type.name}, status={self.status})"


# --- Mock Database/State Management ---
# In a real system, this would be an interface to a persistent data store (e.g., PostgreSQL, DynamoDB).
# It simulates atomic updates and state retrieval.
class MockDataManager:
    """
    A mock data manager to simulate interactions with a database for entity states
    and dispatch queue items. Implements optimistic locking for entity updates.
    """
    _entity_states: Dict[str, EntityState] = {}
    _dispatch_queue: Dict[str, DispatchQueueItem] = {}

    @classmethod
    def get_entity_state(cls, entity_id: str) -> Optional[EntityState]:
        """Retrieves the current state of an entity."""
        return cls._entity_states.get(entity_id)

    @classmethod
    def update_entity_state(cls, entity_state: EntityState, expected_version: int) -> bool:
        """
        Atomically updates an entity's state if its version matches `expected_version`.
        This simulates optimistic locking: if the entity was modified by another
        process, the update fails, preventing stale writes.
        Returns True if the update was successful, False if a version mismatch occurred.
        """
        current_state = cls._entity_states.get(entity_state.entity_id)
        if current_state and current_state.version == expected_version:
            # Increment version on successful update to reflect change
            entity_state.version += 1
            cls._entity_states[entity_state.entity_id] = entity_state
            logger.debug(f"Updated entity {entity_state.entity_id} to state {entity_state.lifecycle_state.name}, new version {entity_state.version}")
            return True
        elif current_state and current_state.version != expected_version:
            logger.warning(
                f"Optimistic lock failure for entity {entity_state.entity_id}. "
                f"Expected version {expected_version}, but found {current_state.version}. "
                f"Current state: {current_state.lifecycle_state.name}"
            )
            return False
        else:
            logger.error(f"Attempted to update non-existent entity {entity_state.entity_id}")
            return False

    @classmethod
    def get_queue_item(cls, item_id: str) -> Optional[DispatchQueueItem]:
        """Retrieves a dispatch queue item."""
        return cls._dispatch_queue.get(item_id)

    @classmethod
    def update_queue_item_status(cls, item_id: str, new_status: str) -> None:
        """Updates the status of a dispatch queue item."""
        item = cls._dispatch_queue.get(item_id)
        if item:
            item.status = new_status
            logger.info(f"Updated queue item {item_id} status to {new_status}")
        else:
            logger.warning(f"Queue item {item_id} not found for status update.")

    @classmethod
    def add_entity(cls, entity: EntityState):
        """Adds a new entity to the mock store."""
        cls._entity_states[entity.entity_id] = entity
        logger.info(f"Added entity {entity.entity_id} with state {entity.lifecycle_state.name}")

    @classmethod
    def add_queue_item(cls, item: DispatchQueueItem):
        """Adds a new dispatch queue item to the mock store."""
        cls._dispatch_queue[item.item_id] = item
        logger.info(f"Added queue item {item.item_id} for target {item.target_entity_id}")


# --- Orchestrator Dispatch Reconciler ---

class ReconciliationDecision(Enum):
    """
    Represents the outcome of a reconciliation policy check.
    Indicates whether a dispatch should proceed or why it should be rejected/deferred.
    """
    PROCEED_WITH_DISPATCH = auto()  # All checks passed, proceed to attempt dispatch.
    ALREADY_SATISFIED = auto()      # Entity is already in desired state by this dispatch, mark queue item completed.
    TERMINAL_STATE = auto()         # Entity is in a terminal state, cannot dispatch.
    STALE_QUEUE_ITEM = auto()       # Queue item is not in QUEUED state.
    CONFLICTING_ACTIVE_DISPATCH = auto() # Another active dispatch owns the entity.
    INCONSISTENT_ENTITY_STATE = auto() # Entity is active but has no associated dispatch_id (data inconsistency).
    UNEXPECTED_STATE_COMBINATION = auto() # Catch-all for other non-dispatchable states.

class DispatchReconciler:
    """
    The orchestrator's dispatch reconciler responsible for ensuring consistency
    between dispatch requests in the queue and the actual state of target entities.
    It addresses divergences caused by partial dispatch failures or concurrent
    lifecycle state changes.
    """
    def __init__(self, data_manager: MockDataManager):
        self.data_manager = data_manager
        self.max_retries = 5  # Maximum attempts for optimistic locking conflicts
        self.retry_delay_ms = 50  # Delay between retries in milliseconds

    def _evaluate_dispatch_policy(self, entity_state: EntityState, queue_item: DispatchQueueItem) -> ReconciliationDecision:
        """
        Evaluates the dispatch policy for the given queue item and entity state.
        This is a critical point for enforcing invariants and preventing stale/duplicate transitions.
        This function is pure and does not modify any state.
        """
        # 1. Check if the queue item itself is no longer "QUEUED".
        # This check is first as an already processed/invalid queue item shouldn't trigger entity state evaluation.
        if queue_item.status != "QUEUED":
            logger.debug(f"Policy check: Queue item {queue_item.item_id} is not in QUEUED status ({queue_item.status}). Decision: {ReconciliationDecision.STALE_QUEUE_ITEM.name}")
            return ReconciliationDecision.STALE_QUEUE_ITEM

        # 2. Check if the entity is already in a terminal state.
        if entity_state.lifecycle_state in [LifecycleState.COMPLETED, LifecycleState.FAILED, LifecycleState.CANCELLED]:
            logger.debug(f"Policy check: Entity {entity_state.entity_id} is already in a terminal state ({entity_state.lifecycle_state.name}). Decision: {ReconciliationDecision.TERMINAL_STATE.name}")
            return ReconciliationDecision.TERMINAL_STATE

        # 3. Check for existing active dispatches or if this dispatch is already satisfied.
        if entity_state.lifecycle_state in [LifecycleState.SCHEDULED, LifecycleState.RUNNING]:
            if entity_state.dispatch_id == queue_item.item_id:
                # The entity is already scheduled/running by this specific dispatch.
                # The intent of this queue item has been satisfied.
                logger.info(f"Policy check: Entity {entity_state.entity_id} is already {entity_state.lifecycle_state.name} by dispatch {entity_state.dispatch_id}. Queue item {queue_item.item_id} is considered satisfied. Decision: {ReconciliationDecision.ALREADY_SATISFIED.name}")
                return ReconciliationDecision.ALREADY_SATISFIED
            elif entity_state.dispatch_id is not None and entity_state.dispatch_id != queue_item.item_id:
                # The entity is actively managed by a *different* dispatch.
                # This queue item represents a stale, duplicate, or policy-violating request.
                logger.warning(
                    f"Policy check: Entity {entity_state.entity_id} is {entity_state.lifecycle_state.name} "
                    f"with active dispatch {entity_state.dispatch_id}, but queue item {queue_item.item_id} "
                    f"attempts to dispatch it again. Conflicting dispatch detected. Decision: {ReconciliationDecision.CONFLICTING_ACTIVE_DISPATCH.name}"
                )
                return ReconciliationDecision.CONFLICTING_ACTIVE_DISPATCH
            else: # entity_state.lifecycle_state is SCHEDULED/RUNNING but dispatch_id is None
                # This is an inconsistent state. An active entity should ideally have a linked dispatch_id.
                logger.error(f"Policy check: Inconsistent state: Entity {entity_state.entity_id} is {entity_state.lifecycle_state.name} but has no associated dispatch_id. Decision: {ReconciliationDecision.INCONSISTENT_ENTITY_STATE.name}")
                return ReconciliationDecision.INCONSISTENT_ENTITY_STATE

        # 4. If the entity is PENDING, it's a valid candidate for dispatch.
        if entity_state.lifecycle_state == LifecycleState.PENDING:
            logger.debug(f"Policy check: Entity {entity_state.entity_id} is PENDING and queue item {queue_item.item_id} is QUEUED. Decision: {ReconciliationDecision.PROCEED_WITH_DISPATCH.name}")
            return ReconciliationDecision.PROCEED_WITH_DISPATCH

        # For any other state combinations, assume reconciliation is not currently appropriate
        logger.debug(f"Policy check: No valid dispatch path for entity {entity_state.entity_id} (state: {entity_state.lifecycle_state.name}) and queue item {queue_item.item_id} (status: {queue_item.status}). Decision: {ReconciliationDecision.UNEXPECTED_STATE_COMBINATION.name}")
        return ReconciliationDecision.UNEXPECTED_STATE_COMBINATION

    def reconcile_dispatch(self, queue_item_id: str) -> bool:
        """
        Attempts to reconcile a single dispatch queue item with its target entity's state.
        This method is designed to handle concurrency and state divergence.
        It implements a retry mechanism with optimistic locking.

        Returns True if the dispatch was successfully reconciled (entity state updated and queue item completed),
        False otherwise (e.g., policy violation, concurrent modification, entity not found).
        """
        for attempt in range(self.max_retries):
            # 1. Fetch the latest state of the queue item
            queue_item = self.data_manager.get_queue_item(queue_item_id)
            if not queue_item:
                logger.warning(f"Reconcile skipped: Queue item {queue_item_id} not found (might have been processed or deleted).")
                return False

            entity_id = queue_item.target_entity_id
            # 2. Fetch the latest state of the target entity
            current_entity_state = self.data_manager.get_entity_state(entity_id)

            if not current_entity_state:
                logger.error(f"Reconcile failed: Target entity {entity_id} for queue item {queue_item_id} not found. Marking queue item as FAILED.")
                self.data_manager.update_queue_item_status(queue_item_id, "FAILED")
                return False

            # --- CRITICAL STEP: Enforce invariant BEFORE committing any state changes ---
            # This is where the divergence check and policy enforcement happens.
            # _evaluate_dispatch_policy returns a decision, which is then acted upon.
            decision = self._evaluate_dispatch_policy(current_entity_state, queue_item)

            if decision != ReconciliationDecision.PROCEED_WITH_DISPATCH:
                # If the policy does not allow proceeding, update the queue item status
                # based on the decision and exit without retrying.
                if decision == ReconciliationDecision.ALREADY_SATISFIED:
                    # The entity was already in the desired active state due to this dispatch.
                    # This means the dispatch itself was effective, but the queue item wasn't marked.
                    # Marking it as SATISFIED_ALREADY_ACTIVE provides a clearer audit trail.
                    self.data_manager.update_queue_item_status(queue_item_id, "SATISFIED_ALREADY_ACTIVE")
                elif decision == ReconciliationDecision.STALE_QUEUE_ITEM:
                    # Queue item status is already not "QUEUED", so no need to update it again.
                    # This check is primarily defensive as _evaluate_dispatch_policy already checks this.
                    pass 
                elif decision in [ReconciliationDecision.TERMINAL_STATE, ReconciliationDecision.CONFLICTING_ACTIVE_DISPATCH, ReconciliationDecision.UNEXPECTED_STATE_COMBINATION]:
                    self.data_manager.update_queue_item_status(queue_item_id, "SKIPPED_POLICY_VIOLATION")
                elif decision == ReconciliationDecision.INCONSISTENT_ENTITY_STATE:
                    # This implies a deeper system issue; defer or fail explicitly for operators to investigate.
                    self.data_manager.update_queue_item_status(queue_item_id, "FAILED")
                
                # Log the decision and return. No retry needed for policy violations.
                logger.info(f"Reconciliation for queue item {queue_item_id} was decided as {decision.name}. No dispatch attempt will be made.")
                return False

            # If we reach here, decision is PROCEED_WITH_DISPATCH.
            # Now, attempt to transition the entity's lifecycle state to SCHEDULED.
            new_entity_state = EntityState(
                entity_id=current_entity_state.entity_id,
                entity_type=current_entity_state.entity_type,
                lifecycle_state=LifecycleState.SCHEDULED,  # Assume dispatch leads to 'SCHEDULED' state
                version=current_entity_state.version,      # Pass current version for optimistic locking
                dispatch_id=queue_item_id                  # Link the dispatch ID to the entity
            )

            # Attempt to atomically update the entity state in the data store.
            # The `update_entity_state` method will return False if another process
            # has modified `current_entity_state` since we fetched it (version mismatch).
            update_successful = self.data_manager.update_entity_state(new_entity_state, current_entity_state.version)

            if update_successful:
                logger.info(f"Successfully dispatched entity {entity_id} for queue item {queue_item_id}. Transitioned to {LifecycleState.SCHEDULED.name}.")
                # If the entity state update is successful, mark the queue item as completed.
                self.data_manager.update_queue_item_status(queue_item_id, "COMPLETED")
                # At this point, further actions like sending the dispatch payload to an agent
                # or updating routing tables would typically occur.
                return True
            else:
                # Optimistic lock failed: a concurrent modification occurred.
                if attempt < self.max_retries - 1:
                    logger.warning(
                        f"Optimistic lock conflict for entity {entity_id} (queue item {queue_item_id}) "
                        f"on attempt {attempt+1}/{self.max_retries}. Retrying after {self.retry_delay_ms}ms. "
                        f"Expected version: {current_entity_state.version}"
                    )
                    time.sleep(self.retry_delay_ms / 1000.0)
                else:
                    logger.error(
                        f"Failed to reconcile dispatch for entity {entity_id} (queue item {queue_item_id}) "
                        f"after {self.max_retries} attempts due to persistent state divergence (concurrency conflict). "
                        f"Marking queue item as FAILED_CONCURRENCY_CONFLICT."
                    )
                    self.data_manager.update_queue_item_status(queue_item_id, "FAILED_CONCURRENCY_CONFLICT")
                    return False
        return False # Should theoretically not be reached if max_retries > 0