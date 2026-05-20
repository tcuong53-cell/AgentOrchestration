import logging

# Configure logging for the module
logger = logging.getLogger(__name__)
# In a real application, handlers and level would be configured centrally.
# For demonstration purposes, we set a basic StreamHandler.
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
logger.setLevel(logging.INFO) # Default level for production use

# --- Custom Exceptions ---
class CompensationError(Exception):
    """Base exception for compensation engine errors."""
    pass

class TaskNotFoundError(CompensationError):
    """Raised when a specified task is not found."""
    pass

class CompensationInvariantViolationError(CompensationError):
    """
    Raised when a core invariant related to compensating actions
    (e.g., 'block downstream after partial rollback') is violated.
    """
    pass

# --- Mock/Helper Classes (to make CompensationEngine runnable conceptually) ---
# In a real system, these would be actual dependencies imported from other modules
# (e.g., from workflow.manager, policy.enforcer) and would interact with a database
# or distributed state store.
class WorkflowManager:
    """
    Mock WorkflowManager to simulate interaction with workflow state.
    Provides methods to get/update task state and manage rollback flags.
    """
    def __init__(self):
        # Stores task_id -> {"state": "RUNNING", "rollback_active": False, ...}
        self.tasks: dict[str, dict] = {}

    def get_task(self, task_id: str) -> dict | None:
        """Retrieves task details by ID."""
        return self.tasks.get(task_id)

    def update_task_state(self, task_id: str, new_state: str):
        """Updates the state of a specific task."""
        if task_id in self.tasks:
            old_state = self.tasks[task_id]["state"]
            self.tasks[task_id]["state"] = new_state
            logger.info(f"WorkflowManager: Task {task_id} state updated from '{old_state}' to '{new_state}'")
        else:
            logger.warning(f"WorkflowManager: Task {task_id} not found for state update.")

    def is_partial_rollback_active(self, task_id: str) -> bool:
        """Checks if a partial rollback is currently active for a task."""
        return self.tasks.get(task_id, {}).get("rollback_active", False)

    def log_critical_event(self, task_id: str, event_type: str, message: str):
        """
        Logs a critical event for monitoring and alerting.
        In a production system, this would integrate with an alerting system or dedicated audit log.
        """
        logger.critical(f"CRITICAL EVENT for task {task_id} ({event_type}): {message}")

    def add_task(self, task_id: str, initial_state: str = "PENDING"):
        """Adds a new task for simulation purposes."""
        if task_id not in self.tasks:
            self.tasks[task_id] = {"task_id": task_id, "state": initial_state, "rollback_active": False}
            logger.info(f"WorkflowManager: Added task {task_id} with state '{initial_state}'")
        else:
            logger.debug(f"WorkflowManager: Task {task_id} already exists.")

    def set_rollback_active(self, task_id: str, active: bool):
        """Sets the rollback active flag for a specific task."""
        if task_id in self.tasks:
            self.tasks[task_id]["rollback_active"] = active
            logger.info(f"WorkflowManager: Rollback active for {task_id} set to: {active}")
        else:
            logger.warning(f"WorkflowManager: Task {task_id} not found to set rollback active flag.")


class PolicyEnforcer:
    """
    Mock PolicyEnforcer to simulate policy checks related to workflow state transitions.
    This component defines rules like 'block downstream after partial rollback'.
    In a production system, this would be a dedicated policy evaluation service,
    potentially reading rules from a configuration or rule engine.
    """
    def is_downstream_blocked(self, task_id: str, current_state: str, target_state: str, is_rollback_active: bool) -> bool:
        """
        Determines if downstream operations should be blocked based on current policies.
        This method is crucial for enforcing the 'block downstream after partial rollback' invariant.

        The core bug implies that when 'is_rollback_active' is True, the policy *should*
        lead to downstream blocking for most transitions, but this was not being enforced.
        """
        if is_rollback_active:
            logger.debug(f"PolicyEnforcer: Rollback active for {task_id}. Checking specific downstream block policy.")
            
            # During an active partial rollback, the general policy is often to block
            # new downstream operations to prevent further state inconsistencies.
            # Exceptions are made for states that are explicitly part of rollback completion or cleanup.
            rollback_completion_states = frozenset({
                "ROLLBACK_COMPLETED", "COMPENSATED", "FAILED_COMPENSATION", 
                "ROLLING_BACK_FAILED", "CLEANUP_REQUIRED", "REVIEW_PENDING", 
                "REVERTED", "IDLE_AFTER_ROLLBACK"
            })
            
            if target_state not in rollback_completion_states:
                logger.info(f"PolicyEnforcer: For task {task_id}, policy dictates downstream MUST BE BLOCKED "
                            f"during active rollback for target state '{target_state}'.")
                return True
            else:
                logger.info(f"PolicyEnforcer: For task {task_id}, target state '{target_state}' is a defined "
                            f"rollback-completion state; NOT blocking downstream via policy for this step.")
                return False

        # General policy checks (unrelated to active rollback, but could exist)
        # Example: If a task failed and is not being explicitly retried or compensated, block downstream.
        if current_state == "FAILED" and target_state not in {"RETRIED", "COMPENSATED", "RECOVERED"}:
            logger.info(f"PolicyEnforcer: Task {task_id} is in FAILED state, not retrying/compensating. Downstream blocked.")
            return True

        logger.debug(f"PolicyEnforcer: No explicit downstream block policy for {task_id} for this scenario.")
        return False


# --- Compensation Engine ---
class CompensationEngine:
    """
    Manages the application of compensating actions in a workflow.
    This component addresses the bug of not enforcing 'block downstream
    after partial rollback' by adding a robust invariant check *before*
    committing any state changes.
    """
    def __init__(self, workflow_manager: WorkflowManager, policy_enforcer: PolicyEnforcer):
        self.workflow_manager = workflow_manager
        self.policy_enforcer = policy_enforcer
        # Define states that, if targeted during an active rollback and when downstream
        # is meant to be blocked, would imply a policy violation. These are states
        # that typically trigger new downstream work or implicitly unblock existing flows.
        # This set should be carefully maintained and reviewed based on overall workflow semantics.
        self._unblocking_states = frozenset({"RUNNING", "SCHEDULED", "COMPLETED", "SUCCESS", "PENDING", "ACTIVE", "READY_TO_START"})

    def _determine_target_state_from_compensation(self, task: dict, compensation_plan: dict) -> str:
        """
        Placeholder for logic that determines the intended state after applying
        a compensation plan. This would typically depend on the specific plan
        (e.g., reverting to a previous stable state, marking as compensated, retrying).
        For this demonstration:
        - If rollback is active, it typically moves to a rollback-completion state.
        - Otherwise, it assumes a general successful compensation leads to a 'COMPENSATED' state.
        A robust implementation would parse `compensation_plan` to derive the precise state.
        """
        if task.get("rollback_active"):
            logger.debug(f"Determining target state: Rollback active for task {task['task_id']}. Suggesting 'ROLLBACK_COMPLETED'.")
            return "ROLLBACK_COMPLETED"
        
        logger.debug(f"Determining target state: No active rollback for task {task['task_id']}. Suggesting 'COMPENSATED'.")
        return "COMPENSATED"

    def _enforce_compensating_actions_invariant(self, task_id: str, current_state: str, target_state: str):
        """
        Ensures that the 'block downstream after partial rollback' invariant is met.
        This is the core of the fix. It performs a multi-pronged check:
        1. Checks if a partial rollback is currently active for the task.
        2. If active, it consults the PolicyEnforcer to determine if downstream
           operations *must* be blocked for this scenario.
        3. If blocking is required by policy, it then checks if the proposed
           target state would implicitly violate this block (by being an 'unblocking' state).

        Raises CompensationInvariantViolationError if the invariant is violated.
        """
        is_rollback_active = self.workflow_manager.is_partial_rollback_active(task_id)
        
        if is_rollback_active:
            logger.debug(f"Invariant check: Partial rollback is active for task {task_id}.")
            
            # Step 2: Check if policy requires downstream to be blocked during this active rollback.
            if self.policy_enforcer.is_downstream_blocked(task_id, current_state, target_state, is_rollback_active):
                logger.debug(f"Invariant check: PolicyEnforcer indicates downstream MUST BE BLOCKED for task {task_id}.")
                
                # Step 3: If policy demands blocking, ensure the proposed target state doesn't implicitly unblock.
                if target_state in self._unblocking_states:
                    error_message = (
                        f"Compensating actions invariant violated for task {task_id}: "
                        f"Partial rollback is active, policy requires downstream to be blocked, "
                        f"but proposed transition to '{target_state}' would implicitly unblock downstream "
                        f"and accept a policy-violating state transition."
                    )
                    logger.error(error_message)
                    raise CompensationInvariantViolationError(error_message)
                else:
                    logger.info(f"Invariant check passed for task {task_id}: Rollback active, policy blocks downstream, "
                                f"and target state '{target_state}' is a safe, non-unblocking state (as expected).")
            else:
                # This branch implies the policy explicitly permits the transition even during rollback
                # (e.g., to a specific cleanup or re-evaluation state that doesn't trigger new work).
                logger.info(f"Invariant check passed for task {task_id}: Rollback active, but policy *does not* require "
                            f"downstream blocking for this specific transition to '{target_state}'.")
        else:
            # No partial rollback active, so this specific invariant ("block downstream after partial rollback")
            # is not applicable. Other general workflow invariants might apply, but are beyond the scope of this fix.
            logger.debug(f"Invariant check passed for task {task_id}: No partial rollback active, invariant not applicable.")

    def apply_compensation(self, task_id: str, compensation_plan: dict):
        """
        Applies a compensation plan to a specific task.
        This method is modified to enforce the compensating actions invariant
        *before* allowing any state changes, directly addressing the reported bug.
        """
        current_task = self.workflow_manager.get_task(task_id)
        if not current_task:
            logger.error(f"Attempted compensation for non-existent task {task_id}.")
            raise TaskNotFoundError(f"Task {task_id} not found.")

        current_state = current_task["state"]
        # Determine the intended target state based on the compensation plan
        target_state_after_compensation = self._determine_target_state_from_compensation(current_task, compensation_plan)

        logger.info(f"\n--- Initiating Compensation for Task {task_id} ---")
        logger.info(f"Current State: {current_state}")
        logger.info(f"Proposed Target State: {target_state_after_compensation}")
        logger.info(f"Rollback Active (via WM): {self.workflow_manager.is_partial_rollback_active(task_id)}")

        # --- CORE FIX: Enforce the compensating actions invariant *before* committing any state changes ---
        # This is the critical step to ensure the 'block downstream after partial rollback' rule
        # is enforced and prevents stale, duplicate, or policy-violating transitions.
        try:
            self._enforce_compensating_actions_invariant(task_id, current_state, target_state_after_compensation)
        except CompensationInvariantViolationError as e:
            # If the invariant is violated, log a critical event and re-raise to prevent the erroneous transition.
            # This fulfills the acceptance criteria to "rejects or safely defers the invalid transition"
            # and "preserves the expected lifecycle state".
            self.workflow_manager.log_critical_event(task_id, "COMPENSATION_INVARIANT_VIOLATION", str(e))
            logger.error(f"Compensation for task {task_id} failed due to invariant violation. State preserved.")
            raise # Re-raise to signal failure and prevent state update
        # --- END CORE FIX ---

        # If the invariant holds, proceed with the actual state update and other compensation logic.
        logger.info(f"Invariant checks passed. Proceeding to update state for task {task_id}.")
        self.workflow_manager.update_task_state(task_id, target_state_after_compensation)

        # Simulate further compensation steps (e.g., notifying other components, clean-up resources)
        # In a real system, this might involve:
        # self.workflow_manager.notify_downstream_components(task_id, target_state_after_compensation)
        # self.workflow_manager.release_resources(task_id)
        logger.info(f"Compensation for task {task_id} successfully applied. New state: {target_state_after_compensation}")


# --- Example Usage (for demonstrating and testing the fix) ---
# This block serves as the deterministic regression test mentioned in the acceptance criteria.
if __name__ == "__main__":
    # For demonstration, set the logger level to DEBUG to see detailed internal workings.
    # In a production environment, this would typically be INFO or WARNING.
    logger.setLevel(logging.DEBUG)
    logging.getLogger("WorkflowManager").setLevel(logging.DEBUG)
    logging.getLogger("PolicyEnforcer").setLevel(logging.DEBUG)

    workflow_manager = WorkflowManager()
    policy_enforcer = PolicyEnforcer()
    compensation_engine = CompensationEngine(workflow_manager, policy_enforcer)

    print("\n--- TEST SCENARIO 1: Normal Compensation (No Rollback Active) ---")
    workflow_manager.add_task("task_A", "FAILED")
    try:
        compensation_engine.apply_compensation("task_A", {"type": "revert"})
        print("Scenario 1 PASSED: Compensation applied successfully (task_A).")
    except CompensationError as e:
        print(f"Scenario 1 FAILED: Unexpected error: {e}")
    assert workflow_manager.get_task("task_A")["state"] == "COMPENSATED", "Scenario 1 state assertion failed."

    print("\n--- TEST SCENARIO 2: Rollback Active, Violating Transition (to RUNNING) ---")
    workflow_manager.add_task("task_B", "PARTIAL_ROLLBACK")
    workflow_manager.set_rollback_active("task_B", True)
    
    # Temporarily override _determine_target_state_from_compensation to simulate a bugged plan
    original_determine_target = compensation_engine._determine_target_state_from_compensation
    compensation_engine._determine_target_state_from_compensation = \
        lambda t, p: "RUNNING" if t["task_id"] == "task_B" else original_determine_target(t, p)

    try:
        compensation_engine.apply_compensation("task_B", {"type": "resume_prematurely"})
        print("Scenario 2 FAILED: Expected CompensationInvariantViolationError for task_B, but it passed.")
        assert False, "Scenario 2: Expected invariant violation was not raised."
    except CompensationInvariantViolationError as e:
        print(f"Scenario 2 PASSED: Caught expected invariant violation for task_B: {e}")
        assert workflow_manager.get_task("task_B")["state"] == "PARTIAL_ROLLBACK", "Scenario 2: Task state should not have changed."
    except CompensationError as e:
        print(f"Scenario 2 FAILED: Caught unexpected error type for task_B: {e}")
        assert False, f"Scenario 2: Caught unexpected error type: {e}"
    finally:
        # Reset the mock for subsequent tests
        compensation_engine._determine_target_state_from_compensation = original_determine_target

    print("\n--- TEST SCENARIO 3: Rollback Active, Safe Transition (to ROLLBACK_COMPLETED) ---")
    workflow_manager.add_task("task_C", "ROLLING_BACK")
    workflow_manager.set_rollback_active("task_C", True)
    # The default _determine_target_state_from_compensation should suggest "ROLLBACK_COMPLETED", which is safe.
    try:
        compensation_engine.apply_compensation("task_C", {"type": "complete_rollback"})
        print("Scenario 3 PASSED: Compensation applied successfully (task_C to safe state).")
    except CompensationError as e:
        print(f"Scenario 3 FAILED: Unexpected error for task_C: {e}")
    assert workflow_manager.get_task("task_C")["state"] == "ROLLBACK_COMPLETED", "Scenario 3 state assertion failed."

    print("\n--- TEST SCENARIO 4: Task Not Found ---")
    try:
        compensation_engine.apply_compensation("task_D_non_existent", {"type": "revert"})
        print("Scenario 4 FAILED: Expected TaskNotFoundError for task_D, but it passed.")
        assert False, "Scenario 4: Expected TaskNotFoundError was not raised."
    except TaskNotFoundError as e:
        print(f"Scenario 4 PASSED: Caught expected TaskNotFoundError for task_D: {e}")
    except CompensationError as e:
        print(f"Scenario 4 FAILED: Caught unexpected error type for task_D: {e}")
        assert False, f"Scenario 4: Caught unexpected error type: {e}"

    print("\n--- TEST SCENARIO 5: Rollback Active, Policy Allows Specific Transition (e.g., to RE_EVALUATE) ---")
    # This tests the scenario where PolicyEnforcer itself says "not blocked" even during rollback for specific states.
    workflow_manager.add_task("task_E", "PARTIAL_ROLLBACK_REVIEW")
    workflow_manager.set_rollback_active("task_E", True)
    
    # Temporarily override policy and target state for this scenario
    original_is_downstream_blocked = policy_enforcer.is_downstream_blocked
    # For task_E transitioning to 'RE_EVALUATE', policy explicitly allows (does NOT block downstream).
    policy_enforcer.is_downstream_blocked = \
        lambda tid, cs, ts, ira: False if tid == "task_E" and ts == "RE_EVALUATE" else original_is_downstream_blocked(tid, cs, ts, ira)
    
    # Set the target state for this test
    compensation_engine._determine_target_state_from_compensation = \
        lambda t, p: "RE_EVALUATE" if t["task_id"] == "task_E" else original_determine_target(t, p)

    try:
        compensation_engine.apply_compensation("task_E", {"type": "re_evaluate_after_rollback"})
        print("Scenario 5 PASSED: Compensation applied successfully (task_E, policy allowed specific transition).")
    except CompensationInvariantViolationError as e:
        print(f"Scenario 5 FAILED: Expected scenario to pass for task_E, but caught invariant violation: {e}")
        assert False, f"Scenario 5: Caught unexpected invariant violation: {e}"
    except CompensationError as e:
        print(f"Scenario 5 FAILED: Caught unexpected error type for task_E: {e}")
        assert False, f"Scenario 5: Caught unexpected error type: {e}"
    finally:
        # Reset mocks
        policy_enforcer.is_downstream_blocked = original_is_downstream_blocked
        compensation_engine._determine_target_state_from_compensation = original_determine_target
    assert workflow_manager.get_task("task_E")["state"] == "RE_EVALUATE", "Scenario 5 state assertion failed."

    print("\n--- All test scenarios completed ---")