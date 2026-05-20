import asyncio
import logging
from typing import Any, Dict, Callable, Optional, Awaitable

# Configure logging for the module
logger = logging.getLogger(__name__)


class TaskStatus:
    """
    Defines standard statuses for task executions.
    """
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class AgentExecutor:
    """
    Manages the execution and lifecycle of agent tasks, ensuring all executions
    have a stored terminal result, especially upon cancellation.
    """
    def __init__(self):
        """
        Initializes the AgentExecutor with an in-memory store for execution results.
        In a production environment, this would typically interact with a persistent
        storage solution (e.g., database, distributed cache).
        """
        self._execution_results: Dict[str, Dict[str, Any]] = {}
        logger.info("AgentExecutor initialized.")

    async def _run_task(self, task: Callable[[Dict[str, Any]], Awaitable[Any]], context: Dict[str, Any]) -> Any:
        """
        Executes the actual task logic. This method is a placeholder for the
        agent's core execution flow. It expects 'task' to be an awaitable
        (e.g., an async function) that takes the context dictionary.
        """
        logger.debug(f"Executing internal task with context: {context}")
        # Simulate some asynchronous work. In a real agent, this would be the
        # orchestration of tool calls, LLM interactions, etc.
        await asyncio.sleep(0.1)
        
        # Assuming 'task' is an async function that accepts 'context'
        return await task(context)

    def _store_result(self, execution_id: str, result_data: Dict[str, Any], status: str):
        """
        Stores the execution result and its final status.
        This method is crucial for clients to poll and understand the state
        of long-running executions.
        """
        self._execution_results[execution_id] = {
            "status": status,
            "result": result_data,
            "timestamp": asyncio.get_event_loop().time()
        }
        logger.info(f"Stored result for execution_id '{execution_id}' with status '{status}'.")

    def get_execution_result(self, execution_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieves the stored result for a given execution ID.
        """
        return self._execution_results.get(execution_id)

    async def execute(self, execution_id: str, task: Callable[[Dict[str, Any]], Awaitable[Any]], context: Dict[str, Any]) -> Any:
        """
        Initiates and manages the execution of a task.

        This method is responsible for:
        1. Setting the initial execution status.
        2. Running the task.
        3. Storing the final result (completion, failure, or cancellation).
        4. Propagating exceptions for upstream handling.

        Args:
            execution_id (str): A unique identifier for this execution.
            task (Callable[[Dict[str, Any]], Awaitable[Any]]): The asynchronous task (coroutine function)
                                                              to be executed, accepting a context dictionary.
            context (Dict[str, Any]): Contextual data required by the task.

        Returns:
            Any: The result of the task execution if successful.

        Raises:
            asyncio.CancelledError: If the execution is externally cancelled.
            Exception: For any other errors encountered during task execution.
        """
        logger.info(f"Initiating execution for ID: {execution_id}")
        self._store_result(execution_id, {"message": "Execution started."}, status=TaskStatus.PENDING)

        try:
            self._store_result(execution_id, {"message": "Execution is running."}, status=TaskStatus.RUNNING)
            
            # The actual core logic of running the agent's task
            final_result = await self._run_task(task, context)
            
            self._store_result(execution_id, final_result, status=TaskStatus.COMPLETED)
            logger.info(f"Execution {execution_id} completed successfully.")
            return final_result
        except asyncio.CancelledError:
            # FIX: Catch CancelledError to store a clear cancelled status.
            # This ensures that polling clients do not wait indefinitely and
            # the system retains a record of the terminal state.
            cancellation_data = {
                "message": f"Execution '{execution_id}' was cancelled by the caller.",
                "details": "The task was terminated before completion."
            }
            self._store_result(execution_id, cancellation_data, status=TaskStatus.CANCELLED)
            logger.warning(f"Execution {execution_id} was cancelled.")
            raise # Re-raise to propagate the cancellation upstream, allowing for further cleanup.
        except Exception as e:
            # Catch any other exceptions and store a failed status.
            error_data = {
                "message": f"Execution '{execution_id}' failed: {str(e)}",
                "error_type": e.__class__.__name__
            }
            self._store_result(execution_id, error_data, status=TaskStatus.FAILED)
            logger.error(f"Execution {execution_id} failed due to an unexpected error: {e}", exc_info=True)
            raise # Re-raise the original exception for upstream handling.