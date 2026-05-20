import os
import shutil
import tempfile
import threading
from contextlib import contextmanager
import json
import logging

# Configure logging for operational messages. In a production environment, this would integrate
# with a centralized logging system and logging levels would be managed via configuration
# (e.g., environment variables). By default, INFO level messages and above are shown;
# DEBUG messages are suppressed.
logger = logging.getLogger(__name__)
if not logger.handlers:
    # Add a default handler if none are configured to prevent "No handlers could be found" warning.
    # We set the default level to INFO, meaning DEBUG messages will not be displayed unless
    # the logging level is explicitly changed (e.g., for troubleshooting).
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


# In a real system, `_run_states` would be backed by a durable, distributed store (e.g., database, Redis).
# Similarly, `_state_lock` would be a robust distributed locking mechanism (e.g., ZooKeeper, Redlock).
# For this file-centric runtime, an in-memory dictionary and threading.Lock serve as conceptual
# placeholders to demonstrate the "durable guard" principle for state consistency within this process.
# This design choice adheres to the problem statement's given context for the fix.
_run_states: dict[str, str] = {}
_state_lock = threading.Lock()

class RunState:
    """Defines the possible states for an orchestration run."""
    INITIALIZED = "INITIALIZED" # Run record exists, initial setup done
    PENDING = "PENDING"         # Work setup in progress / waiting for a worker
    RUNNING = "RUNNING"         # Worker is actively processing the run
    COMPLETED = "COMPLETED"     # Run finished successfully
    FAILED = "FAILED"           # Run finished with an error
    CANCELLED = "CANCELLED"     # Run was explicitly cancelled
    CLEANING = "CLEANING"       # Temporary files are currently being cleaned up
    CLEANED = "CLEANED"         # Temporary files have been successfully cleaned up

class FileRuntime:
    """
    Manages the lifecycle and file system resources for agent orchestration runs.
    Enforces deterministic cleanup and consistent state transitions using a "durable guard"
    principle to prevent inconsistent run states under load.
    """
    def __init__(self, base_temp_dir: str = "temp_agent_runs"):
        """
        Initializes the FileRuntime with a base directory for temporary run files.

        Args:
            base_temp_dir: The root directory where all run-specific temporary directories will be created.
        """
        self.base_temp_dir = base_temp_dir
        os.makedirs(self.base_temp_dir, exist_ok=True)
        logger.info(f"FileRuntime initialized with base temporary directory: {self.base_temp_dir}")

    @contextmanager
    def _acquire_run_lock(self, run_id: str):
        """
        Provides a durable guard for critical sections involving a specific run's state and files.
        This local lock ensures atomicity for in-memory state updates. In a distributed
        environment, this would be replaced by a robust distributed locking mechanism
        to prevent race conditions across multiple workers/schedulers.
        """
        logger.debug(f"Acquiring lock for run_id: {run_id}")
        _state_lock.acquire()
        try:
            yield
        finally:
            _state_lock.release()
            logger.debug(f"Released lock for run_id: {run_id}")

    def _get_run_state(self, run_id: str) -> str | None:
        """Retrieves the current state of a run."""
        with _state_lock:
            return _run_states.get(run_id)

    def _set_run_state(self, run_id: str, state: str):
        """Sets the state of a run."""
        with _state_lock:
            _run_states[run_id] = state
        logger.info(f"Run '{run_id}' state updated to: {state}")

    def _validate_transition(self, run_id: str, allowed_from_states: list[str]):
        """
        Validates if a state transition is allowed from the current run state.
        This function implements the "fails closed" principle: if the current state
        is not in the `allowed_from_states`, a ValueError is raised immediately,
        preventing any further mutation or work.

        Args:
            run_id: The ID of the run.
            allowed_from_states: A list of states from which the transition is permitted.

        Raises:
            ValueError: If the current state is not in allowed_from_states.
        """
        current_state = self._get_run_state(run_id)
        if current_state not in allowed_from_states:
            error_msg = (f"Invalid state transition for run '{run_id}'. "
                         f"Current state is '{current_state}', but expected one of {allowed_from_states}.")
            logger.error(error_msg)
            raise ValueError(error_msg)
        logger.debug(f"State transition for run '{run_id}' from '{current_state}' to a new state is valid.")


    def _create_temp_run_dir(self, run_id: str) -> str:
        """Creates a unique temporary directory for a specific run."""
        run_dir = os.path.join(self.base_temp_dir, run_id)
        try:
            os.makedirs(run_dir, exist_ok=True)
            logger.info(f"Created temporary run directory: {run_dir} for run '{run_id}'")
            return run_dir
        except OSError as e:
            logger.critical(f"Failed to create run directory '{run_dir}' for run '{run_id}': {e}")
            raise RuntimeError(f"Failed to create run directory for '{run_id}'") from e

    def _get_run_dir(self, run_id: str) -> str:
        """Gets the expected temporary directory path for a run."""
        return os.path.join(self.base_temp_dir, run_id)

    def _cleanup_run_files(self, run_id: str):
        """
        Deterministically cleans up temporary files associated with a run.
        This operation is designed to be idempotent and robust to errors.
        If cleanup fails, the run state remains `CLEANING`, allowing for external
        monitoring and retry mechanisms.
        """
        run_dir = self._get_run_dir(run_id)
        current_state = self._get_run_state(run_id) # Get current state before changing to CLEANING

        # Check if the directory already doesn't exist. This makes the cleanup idempotent.
        if not os.path.exists(run_dir):
            if current_state != RunState.CLEANED:
                self._set_run_state(run_id, RunState.CLEANED)
            logger.info(f"Run files for '{run_id}' at '{run_dir}' already cleaned or never existed.")
            return

        logger.info(f"Attempting to clean up run files for '{run_id}' at '{run_dir}'")
        try:
            self._set_run_state(run_id, RunState.CLEANING) # Indicate cleanup is in progress
            shutil.rmtree(run_dir)
            self._set_run_state(run_id, RunState.CLEANED)
            logger.info(f"Successfully cleaned up run files for '{run_id}'.")
        except OSError as e:
            # Log the error but do not re-raise. The state remains `CLEANING`,
            # indicating a pending cleanup task that an external agent should retry.
            logger.error(f"Failed to clean up run files for '{run_id}' at '{run_dir}': {e}")
            # State remains CLEANING; no change required here, external monitoring/retry will handle.


    def initialize_run(self, run_id: str):
        """
        Initializes a run, setting its state to INITIALIZED.
        This creates a record for the run before any files are created or work is dispatched.
        This is the designated entry point for a new run or to re-initialize a run
        that has reached a terminal or cleaned state.

        Args:
            run_id: The unique ID for the run.

        Raises:
            ValueError: If the run cannot be initialized from its current state.
        """
        with self._acquire_run_lock(run_id):
            # Allow initialization if it's a completely new run (None), or if it was
            # previously completed, failed, cancelled, or cleaned.
            self._validate_transition(run_id, [None, RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED, RunState.CLEANED])

            self._set_run_state(run_id, RunState.INITIALIZED)
            logger.info(f"Run '{run_id}' initialized.")


    def start_run(self, run_id: str, payload: dict) -> str:
        """
        Prepares and starts a new run, including setting up its temporary directory
        and writing the initial payload. This method implements the "durable guard"
        by validating state before any mutations.

        Args:
            run_id: The unique ID for the run.
            payload: The initial data/configuration for the run.

        Returns:
            The path to the temporary run directory.

        Raises:
            ValueError: If the run cannot be started from its current state (e.g., not INITIALIZED).
            RuntimeError: If there's an issue creating files or directories.
        """
        with self._acquire_run_lock(run_id):
            # Durable guard 1: Validate state transition.
            # A run can only start if it has been INITIALIZED or successfully CLEANED.
            # This enforces a clear state machine flow, preventing direct start from an unknown state (None).
            self._validate_transition(run_id, [RunState.INITIALIZED, RunState.CLEANED])

            # Perform actions (mutating state, creating files) only after validation succeeds.
            self._set_run_state(run_id, RunState.PENDING)
            run_dir = self._create_temp_run_dir(run_id)

            # Write payload to a file within the run directory
            payload_file = os.path.join(run_dir, "payload.json")
            try:
                with open(payload_file, "w") as f:
                    json.dump(payload, f)
                logger.debug(f"Payload written to {payload_file} for run '{run_id}'.")
            except IOError as e:
                # If file write fails, roll back state and raise error (fails closed).
                # We attempt to clean up the partially created directory as well.
                logger.error(f"Failed to write payload for run '{run_id}'. Rolling back state and cleaning up: {e}")
                self._set_run_state(run_id, RunState.FAILED) # Mark as failed due to setup issue
                self._cleanup_run_files(run_id) # Attempt to clean up
                raise RuntimeError(f"Failed to write payload for run '{run_id}': {e}") from e

            self._set_run_state(run_id, RunState.RUNNING)
            logger.info(f"Run '{run_id}' started and is now in RUNNING state.")
            return run_dir

    def complete_run(self, run_id: str, success: bool, output: dict | None = None):
        """
        Marks a run as completed (either successfully or failed) and triggers deterministic cleanup.
        This method ensures the final state transition and cleanup are handled robustly.

        Args:
            run_id: The ID of the run.
            success: True if the run completed successfully, False otherwise.
            output: Optional, results or error details of the run.

        Raises:
            ValueError: If the run is not in a 'RUNNING' or 'PENDING' state.
        """
        with self._acquire_run_lock(run_id):
            # Durable guard 2: Validate state transition
            # A run can complete from RUNNING or PENDING (e.g., if it failed immediately after dispatch).
            self._validate_transition(run_id, [RunState.RUNNING, RunState.PENDING])

            # Optionally write output to a file before cleaning up, if provided.
            if output:
                run_dir = self._get_run_dir(run_id)
                output_file = os.path.join(run_dir, "output.json")
                try:
                    os.makedirs(os.path.dirname(output_file), exist_ok=True) # Ensure directory exists
                    with open(output_file, "w") as f:
                        json.dump(output, f)
                    logger.debug(f"Output written to {output_file} for run '{run_id}'.")
                except IOError as e:
                    # Log the warning but proceed with cleanup/state change, as this is post-run
                    # and should not block the final state transition. This issue should be externally monitored.
                    logger.warning(f"Failed to write output for run '{run_id}' to '{output_file}': {e}")

            if success:
                self._set_run_state(run_id, RunState.COMPLETED)
                logger.info(f"Run '{run_id}' completed successfully.")
            else:
                self._set_run_state(run_id, RunState.FAILED)
                logger.warning(f"Run '{run_id}' failed.")

            # Durable guard 3: Enforce deterministic cleanup for temporary files
            self._cleanup_run_files(run_id)

    def cancel_run(self, run_id: str):
        """
        Cancels an ongoing or pending run and triggers deterministic cleanup.

        Args:
            run_id: The ID of the run.

        Raises:
            ValueError: If the run is not in a cancellable state.
        """
        with self._acquire_run_lock(run_id):
            # Validate that the run is in a state that can be cancelled.
            self._validate_transition(run_id, [RunState.PENDING, RunState.RUNNING])

            self._set_run_state(run_id, RunState.CANCELLED)
            logger.info(f"Run '{run_id}' cancelled.")

            # Enforce deterministic cleanup for temporary files.
            self._cleanup_run_files(run_id)