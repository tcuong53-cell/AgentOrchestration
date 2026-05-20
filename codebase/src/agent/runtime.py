import os
import subprocess
import logging

# Configure basic logging for the module.
# In a larger application, this would typically be set up centrally.
# For a standalone file, this provides default behavior.
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class AgentRuntime:
    """
    Manages the lifecycle and environment for a child agent process.
    Protects orchestrator-owned environment variables from being overridden by external callers.
    """

    # Define a set of environment variable keys that are considered 'reserved'.
    # These keys are critical for the agent's operation, identity, or security,
    # and their values must be controlled exclusively by the orchestrator.
    # Any attempt by a caller to set these will be rejected immediately.
    RESERVED_ENV_KEYS = {
        "AGENT_ID",               # Unique identifier for the agent instance.
        "ORCHESTRATOR_URL",       # Endpoint for the agent to communicate with the orchestrator.
        "AGENT_MODE",             # Operational mode of the agent (e.g., 'production', 'sandbox', 'debug').
        "RUNTIME_CONFIG_PATH",    # Path to agent-specific configuration files.
        "LOG_LEVEL",              # Controls the verbosity of agent logging.
        "DEBUG_ENABLED",          # Flag to enable/disable debug features.
        "API_KEY",                # Sensitive API key for external service access (if applicable).
        "SVC_NAME",               # Service name, useful for monitoring/tracing.
        # Add any other environment variables crucial for agent integrity and control here.
    }

    def __init__(self, agent_id: str, orchestrator_url: str, config_path: str, agent_mode: str = "production"):
        """
        Initializes the AgentRuntime with core orchestrator-defined parameters.

        Args:
            agent_id: The unique identifier for the agent.
            orchestrator_url: The URL for the agent to connect back to the orchestrator.
            config_path: The file path to the agent's runtime configuration.
            agent_mode: The operational mode of the agent (default: "production").
        """
        self._agent_id = agent_id
        self._orchestrator_url = orchestrator_url
        self._config_path = config_path
        self._agent_mode = agent_mode

        # _orchestrator_owned_env stores the definitive values for environment variables
        # that the orchestrator explicitly controls and injects into the child process.
        # These values will always take precedence over any system or caller-provided values
        # in the final environment merged into the child process.
        self._orchestrator_owned_env = {
            "AGENT_ID": self._agent_id,
            "ORCHESTRATOR_URL": self._orchestrator_url,
            "RUNTIME_CONFIG_PATH": self._config_path,
            "AGENT_MODE": self._agent_mode,
            "LOG_LEVEL": os.environ.get("AGENT_LOG_LEVEL", "INFO"), # Allow host env to set default log level, otherwise INFO.
            "SVC_NAME": f"agent-{self._agent_id}", # Example of a derived value based on agent_id.
            # Any other orchestrator-controlled variables that should take final precedence.
        }

    def start(self, command: list[str], caller_env: dict[str, str] = None) -> subprocess.Popen:
        """
        Launches the agent process with the specified command and environment.
        Ensures that reserved orchestrator-owned environment variables are protected
        from being overridden by external callers and that orchestrator-defined
        values take ultimate precedence.

        Args:
            command: A list of strings representing the executable and its arguments.
            caller_env: An optional dictionary of environment variables provided by the caller.

        Returns:
            A subprocess.Popen object representing the launched agent process.

        Raises:
            ValueError: If the caller attempts to set a reserved environment variable.
            RuntimeError: If the agent executable is not found.
            Exception: For other errors encountered during process launch.
        """
        if caller_env is None:
            caller_env = {}

        # 1. Validate caller_env: Reject any attempt to set reserved keys.
        # This prevents external callers from interfering with critical runtime settings
        # and fulfills the core requirement of the bug fix.
        for key in caller_env:
            if key in self.RESERVED_ENV_KEYS:
                logger.error(f"Caller attempted to set reserved environment variable: '{key}'. This action is prohibited.")
                raise ValueError(
                    f"Attempted to set reserved environment variable '{key}'. "
                    "These keys are protected and set exclusively by the orchestrator. "
                    "Please remove this key from the caller_env."
                )

        # 2. Prepare the base environment for the child process.
        # Start with a copy of the current process's environment variables (`os.environ`).
        # This ensures that standard system environment variables (like PATH) are available
        # to the child process by default.
        child_env = os.environ.copy()

        # 3. Merge caller_env.
        # Environment variables provided by the caller are merged next.
        # Since reserved keys were already rejected, this merge is safe for
        # caller-specific, non-reserved variables.
        child_env.update(caller_env)

        # 4. Enforce orchestrator-owned variables.
        # These values are critical for the agent's correct operation and identity.
        # They are merged last to ensure they take final, unquestionable precedence
        # over any prior settings from `os.environ` or `caller_env`.
        child_env.update(self._orchestrator_owned_env)

        logger.info(f"Launching agent '{self._agent_id}' (mode: {self._agent_mode}) with command: '{' '.join(command)}'")
        # For production, logging the full child_env (even at debug level) should be
        # handled with extreme caution due to potential sensitive information.
        # In a development/debugging scenario, it might be temporarily enabled.
        # logger.debug(f"Final environment for agent '{self._agent_id}': {child_env}")

        try:
            # Launch the subprocess.
            # `text=True` handles stdin/stdout/stderr in text mode.
            # `stdout=subprocess.PIPE` and `stderr=subprocess.PIPE` allow capturing output
            # from the child process, which can be useful for logging or further processing.
            process = subprocess.Popen(
                command,
                env=child_env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            logger.info(f"Agent '{self._agent_id}' process started successfully with PID: {process.pid}")
            return process
        except FileNotFoundError:
            # Catch specific error if the executable is not found.
            logger.exception(f"Agent executable '{command[0]}' not found. "
                             "Please ensure it is in the system's PATH or provided with a full, correct path.")
            raise RuntimeError(f"Command '{command[0]}' not found. "
                               "Ensure the agent executable is installed and accessible in PATH.")
        except Exception as e:
            # Catch any other unexpected exceptions during process launch.
            logger.exception(f"Failed to start agent process for '{self._agent_id}': An unexpected error occurred: {e}")
            raise