import datetime
from typing import List, Dict, Any, Optional
import pytz # Required for robust timezone handling beyond UTC

# --- Constants for audit logging and policy decisions ---
AUDIT_EVENT_TYPE_DISPATCH_DECISION = "dispatch_decision"
AUDIT_STATUS_REJECTED = "rejected"
AUDIT_STATUS_ACCEPTED = "accepted"

class PolicyViolationError(Exception):
    """Custom exception raised when a dispatch policy is violated."""
    pass

def _get_timezone_obj(tz_str: str) -> datetime.tzinfo:
    """
    Returns a timezone object for a given timezone string.
    Falls back to UTC if the timezone string is unknown or invalid.
    """
    try:
        if tz_str == "UTC":
            return datetime.timezone.utc
        return pytz.timezone(tz_str)
    except pytz.UnknownTimeZoneError:
        # In a production system, this would typically log a warning/error
        # without exposing private runtime data. For this exercise,
        # we silently fall back to UTC to prevent crashes.
        return datetime.timezone.utc

class TimeBasedDispatchPolicy:
    """
    A dispatch policy that enforces time-based constraints, including
    workflow-level blackout windows, before allowing any state transitions.
    """
    def __init__(self, workflow_config: Dict[str, Any]):
        """
        Initializes the policy with the workflow's configuration.

        Example workflow_config structure:
        {
           "id": "workflow_123",
           "blackout_windows": [
               {"start": "02:00", "end": "04:00", "type": "daily_time"},
               {"start_datetime": "2023-10-26T10:00:00Z", "end_datetime": "2023-10-26T11:00:00Z", "type": "specific_datetime"}
           ],
           "timezone": "UTC" # Or a specific timezone identifier (e.g., 'America/New_York')
        }
        """
        self.workflow_config = workflow_config
        # Determine the workflow's timezone for interpreting daily blackout windows.
        self.workflow_timezone = _get_timezone_obj(workflow_config.get("timezone", "UTC"))
        # Pre-parse blackout windows for efficiency to avoid repeated string parsing during evaluation.
        self._parsed_blackout_windows = self._parse_blackout_windows(workflow_config.get("blackout_windows", []))

    def _parse_blackout_windows(self, raw_windows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Parses raw blackout window configurations into an optimized internal format
        with pre-computed datetime/time objects. This avoids repeated string parsing
        during policy evaluation, improving performance.
        Malformed or unparseable windows are skipped.
        """
        parsed_windows = []
        for window in raw_windows:
            window_type = window.get("type")
            parsed_window = {"type": window_type}
            try:
                if window_type == "daily_time" and "start" in window and "end" in window:
                    # Daily times represent a time of day relative to the workflow's local timezone.
                    # They are parsed as naive time objects here.
                    parsed_window["start_time"] = datetime.datetime.strptime(window["start"], "%H:%M").time()
                    parsed_window["end_time"] = datetime.datetime.strptime(window["end"], "%H:%M").time()
                    parsed_windows.append(parsed_window)
                elif window_type == "specific_datetime" and "start_datetime" in window and "end_datetime" in window:
                    # Specific datetimes are absolute points in time. Convert them to UTC immediately
                    # for consistent, timezone-agnostic comparison.
                    # Replace 'Z' with '+00:00' for broader compatibility with datetime.fromisoformat.
                    start_dt_str = window["start_datetime"].replace('Z', '+00:00')
                    end_dt_str = window["end_datetime"].replace('Z', '+00:00')
                    parsed_window["start_datetime_utc"] = datetime.datetime.fromisoformat(start_dt_str).astimezone(datetime.timezone.utc)
                    parsed_window["end_datetime_utc"] = datetime.datetime.fromisoformat(end_dt_str).astimezone(datetime.timezone.utc)
                    parsed_windows.append(parsed_window)
                # Any other window types or malformed configurations (e.g., missing start/end) are implicitly skipped.
            except (ValueError, TypeError):
                # Log this error without exposing private data.
                # In a real system, a dedicated logger would record:
                # logger.warning(f"Failed to parse blackout window config: {window}. Skipping this window.")
                pass # Suppress logging in this code output as per instructions.
        return parsed_windows

    def _is_within_workflow_blackout_window(self, current_datetime_utc: datetime.datetime) -> bool:
        """
        Checks if the `current_datetime_utc` falls within any defined blackout windows for the workflow.
        Daily time comparisons are performed using the workflow's configured local timezone.
        Specific datetime comparisons are performed using UTC.
        """
        # Localize the current UTC time to the workflow's configured timezone
        # for proper evaluation of daily blackout windows.
        current_datetime_local = current_datetime_utc.astimezone(self.workflow_timezone)
        current_time_local = current_datetime_local.time()
        
        for window in self._parsed_blackout_windows:
            window_type = window.get("type")
            
            if window_type == "daily_time":
                start_time = window["start_time"]
                end_time = window["end_time"]
                
                # Check if the localized current time falls within the daily range.
                # This correctly handles ranges that cross midnight (e.g., 23:00-02:00).
                if start_time <= end_time:
                    if start_time <= current_time_local < end_time:
                        return True
                else:  # Range crosses midnight
                    if current_time_local >= start_time or current_time_local < end_time:
                        return True
            elif window_type == "specific_datetime":
                start_dt_utc = window["start_datetime_utc"]
                end_dt_utc = window["end_datetime_utc"]
                
                # Check if the current UTC time falls within the specific UTC datetime range.
                if start_dt_utc <= current_datetime_utc < end_dt_utc:
                    return True
        return False

    def evaluate_dispatch(self, task_context: Dict[str, Any]) -> bool:
        """
        Evaluates the dispatch policy for a given task context.
        This method acts as the "atomic state precondition" by performing
        all necessary policy checks based on the current time and configuration.
        Returns True if dispatch is allowed, False otherwise.
        """
        # Get the current time for policy evaluation, ensuring it is timezone-aware (UTC).
        # Using datetime.now(timezone.utc) is preferred over utcnow() for modern Python.
        current_evaluation_time_utc = datetime.datetime.now(datetime.timezone.utc)
        
        # --- CORE FIX: Enforce workflow-level blackout windows *before* any other dispatch logic. ---
        # This is the primary invariant check specified by the bug report.
        if self._is_within_workflow_blackout_window(current_evaluation_time_utc):
            # If currently in a blackout window, dispatch is explicitly not allowed.
            return False
        # --- END CORE FIX ---

        # Placeholder for other potential time-based dispatch logic.
        # This could include checks like a task's scheduled start time, execution windows,
        # rate limits, or concurrency policies that might be integrated into the policy.
        # Example:
        # if task_context.get("scheduled_start_time_utc") and \
        #    current_evaluation_time_utc < task_context["scheduled_start_time_utc"]:
        #    return False # Task is not yet scheduled to start.

        # If all time-based policies (including blackout windows) are met, allow dispatch.
        return True

    def dispatch(self, agent_run_data: Dict[str, Any], task_data: Dict[str, Any], handler_data: Dict[str, Any]):
        """
        Main entry point for dispatching an agent run, task, or handler.
        This method orchestrates scheduling, routing, queueing, or workflow state changes.
        It strictly enforces the time-based dispatch policy invariant before committing any state.
        """
        # Extract relevant IDs for auditing and error reporting.
        workflow_id = self.workflow_config.get("id", "unknown_workflow")
        task_id = task_data.get("id", "unknown_task")

        context = {
            "agent_run": agent_run_data,
            "task": task_data,
            "handler": handler_data,
            "workflow_id": workflow_id,
            "task_id": task_id
        }

        # The critical change to address the bug:
        # Enforce the time-based dispatch policy invariant *before*
        # committing any scheduling, routing, queue, or workflow state.
        if not self.evaluate_dispatch(context):
            # If the policy evaluation returns False, a policy (e.g., blackout window) is violated.
            # We must prevent the state transition to maintain the invariant.
            rejection_reason = "Workflow blackout window active."
            self._record_audit_event(
                status=AUDIT_STATUS_REJECTED,
                workflow_id=workflow_id,
                task_id=task_id,
                reason=rejection_reason,
                details={
                    "policy": "TimeBasedDispatchPolicy",
                    "evaluation_time_utc": datetime.datetime.now(datetime.timezone.utc).isoformat()
                }
            )
            raise PolicyViolationError(
                f"Dispatch policy violated for workflow '{workflow_id}' "
                f"and task '{task_id}'. Reason: {rejection_reason} "
                f"Transition blocked to prevent policy-violating state change."
            )

        # If the policy is honored (evaluate_dispatch returned True),
        # proceed with the actual state commitment operations.
        # These operations were previously occurring without the crucial blackout check.
        self._commit_scheduling_state(context)
        self._route_task_to_queue(context)
        self._update_workflow_state(context)
        
        # Record successful dispatch for audit purposes.
        self._record_audit_event(
            status=AUDIT_STATUS_ACCEPTED,
            workflow_id=workflow_id,
            task_id=task_id,
            reason="Dispatch policy accepted.",
            details={
                "policy": "TimeBasedDispatchPolicy",
                "evaluation_time_utc": datetime.datetime.now(datetime.timezone.utc).isoformat()
            }
        )

    def _commit_scheduling_state(self, context: Dict[str, Any]):
        """
        Placeholder: Commits scheduling-related state to persistence.
        In a real system, this method would interact with a database or other storage
        to persist changes regarding the scheduler's view of tasks/runs.
        Transactional behavior (e.g., database transactions) would be crucial here
        to ensure atomicity of state changes at the system level.
        """
        pass

    def _route_task_to_queue(self, context: Dict[str, Any]):
        """
        Placeholder: Routes the task to an execution queue.
        This method might publish a message to a message queue (e.g., Kafka, RabbitMQ)
        for an agent service to pick up and process.
        """
        pass

    def _update_workflow_state(self, context: Dict[str, Any]):
        """
        Placeholder: Updates the overall workflow state.
        This involves updating the workflow's status, progress, or metadata
        in a persistence layer. Similar to `_commit_scheduling_state`, this
        would need appropriate transactional safeguards.
        """
        pass

    def _record_audit_event(self, status: str, workflow_id: str, task_id: str, reason: str, details: Dict[str, Any]):
        """
        Placeholder for recording audit events.
        This method satisfies the requirement to "persist the decision with bounded audit metadata"
        and provide "Logs, metrics, or audit records explain the decision without exposing private runtime data."
        
        In a real system, this would send data to:
        - A structured logging system (e.g., ELK stack, Splunk)
        - A metrics system (e.g., Prometheus, Datadog)
        - A dedicated audit database/store
        
        It ensures that every dispatch decision (acceptance or rejection) is traceable.
        """
        audit_record = {
            "event_type": AUDIT_EVENT_TYPE_DISPATCH_DECISION,
            "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "status": status,
            "workflow_id": workflow_id,
            "task_id": task_id,
            "reason": reason,
            "details": details # Contains non-private, contextual data for the decision.
        }
        # Example of how this might be used in a real application:
        # logger.info(json.dumps(audit_record))
        # metrics_client.increment(f"dispatch_policy.decisions.{status}", tags={"workflow_id": workflow_id})
        # audit_db_client.insert_record(audit_record)
        pass