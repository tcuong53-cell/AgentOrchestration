"""Agent Runtime — Manages agent process lifecycle."""

import os
import signal
import subprocess
import logging
from enum import Enum
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class RuntimeState(Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    CRASHED = "crashed"


class AgentRuntime:
    def __init__(self):
        self._processes: Dict[str, subprocess.Popen] = {}
        self._states: Dict[str, RuntimeState] = {}

    def start(self, agent_id: str, command: list, env: Optional[Dict] = None) -> bool:
        if agent_id in self._processes and self._processes[agent_id].poll() is None:
            logger.warning(f"Agent {agent_id} is already running")
            return False

        self._states[agent_id] = RuntimeState.STARTING
        process_env = os.environ.copy()
        if env:
            process_env.update(env)
        process_env["AO_AGENT_ID"] = agent_id

        try:
            proc = subprocess.Popen(
                command,
                env=process_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self._processes[agent_id] = proc
            self._states[agent_id] = RuntimeState.RUNNING
            logger.info(f"Agent {agent_id} started (PID: {proc.pid})")
            return True
        except Exception as e:
            self._states[agent_id] = RuntimeState.CRASHED
            logger.error(f"Failed to start agent {agent_id}: {e}")
            return False

    def stop(self, agent_id: str, timeout: int = 10) -> bool:
        proc = self._processes.get(agent_id)
        if not proc or proc.poll() is not None:
            return False

        self._states[agent_id] = RuntimeState.STOPPING
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

        self._states[agent_id] = RuntimeState.STOPPED
        logger.info(f"Agent {agent_id} stopped")
        return True

    def get_state(self, agent_id: str) -> RuntimeState:
        proc = self._processes.get(agent_id)
        if proc and proc.poll() is not None:
            self._states[agent_id] = RuntimeState.CRASHED
        return self._states.get(agent_id, RuntimeState.STOPPED)

    def is_running(self, agent_id: str) -> bool:
        proc = self._processes.get(agent_id)
        return proc is not None and proc.poll() is None

# Add logic to enforce server-side limits before starting an agent
def enforce_server_limits(agent_id):
    # Example logic: Check if the number of running agents exceeds a threshold
    max_running_agents = 10
    current_running_agents = sum(1 for state in self._states.values() if state == RuntimeState.RUNNING)
    
    if current_running_agents >= max_running_agents:
        logger.warning(f"Agent {agent_id} cannot be started as the maximum number of running agents ({max_running_agents}) has been reached.")
        return False
    
    # Add more logic to enforce other server-side limits as needed
    return True

# Modify start method to include server limit enforcement
def start(self, agent_id: str, command: list, env: Optional[Dict] = None) -> bool:
    if not enforce_server_limits(agent_id):
        return False
    
    # Existing code for starting the agent
    self._states[agent_id] = RuntimeState.STARTING
    process_env = os.environ.copy()
    if env:
        process_env.update(env)
    process_env["AO_AGENT_ID"] = agent_id

    try:
        proc = subprocess.Popen(
            command,
            env=process_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._processes[agent_id] = proc
        self._states[agent_id] = RuntimeState.RUNNING
        logger.info(f"Agent {agent_id} started (PID: {proc.pid})")
        return True
    except Exception as e:
        self._states[agent_id] = RuntimeState.CRASHED
        logger.error(f"Failed to start agent {agent_id}: {e}")
        return False

# Add logic to enforce server-side limits before stopping an agent
def enforce_server_limits_for_stop(agent_id):
    # Example logic: Check if the number of running agents exceeds a threshold
    max_running_agents = 10
    current_running_agents = sum(1 for state in self._states.values() if state == RuntimeState.RUNNING)
    
    if current_running_agents >= max_running_agents:
        logger.warning(f"Agent {agent_id} cannot be stopped as the maximum number of running agents ({max_running_agents}) has been reached.")
        return False
    
    # Add more logic to enforce other server-side limits as needed
    return True

# Modify stop method to include server limit enforcement
def stop(self, agent_id: str, timeout: int = 10) -> bool:
    if not enforce_server_limits_for_stop(agent_id):
        return False
    
    # Existing code for stopping the agent
    proc = self._processes.get(agent_id)
    if not proc or proc.poll() is not None:
        return False

    self._states[agent_id] = RuntimeState.STOPPING
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()

    self._states[agent_id] = RuntimeState.STOPPED
    logger.info(f"Agent {agent_id} stopped")
    return True

# Add logic to enforce server-side limits before getting the state of an agent
def enforce_server_limits_for_state(agent_id):
    # Example logic: Check if the number of running agents exceeds a threshold
    max_running_agents = 10
    current_running_agents = sum(1 for state in self._states.values() if state == RuntimeState.RUNNING)
    
    if current_running_agents >= max_running_agents:
        logger.warning(f"Agent {agent_id} cannot be queried as the maximum number of running agents ({max_running_agents}) has been reached.")
        return False
    
    # Add more logic to enforce other server-side limits as needed
    return True

# Modify get_state method to include server limit enforcement
def get_state(self, agent_id: str) -> RuntimeState:
    if not enforce_server_limits_for_state(agent_id):
        return RuntimeState.STOPPED
    
    # Existing code for getting the state of an agent
    proc = self._processes.get(agent_id)
    if proc and proc.poll() is not None:
        self._states[agent_id] = RuntimeState.CRASHED
    return self._states.get(agent_id, RuntimeState.STOPPED)

# Add logic to enforce server-side limits before checking if an agent is running
def enforce_server_limits_for_running(agent_id):
    # Example logic: Check if the number of running agents exceeds a threshold
    max_running_agents = 10
    current_running_agents = sum(1 for state in self._states.values() if state == RuntimeState.RUNNING)
    
    if current_running_agents >= max_running_agents:
        logger.warning(f"Agent {agent_id} cannot be checked as the maximum number of running agents ({max_running_agents}) has been reached.")
        return False
    
    # Add more logic to enforce other server-side limits as needed
    return True

# Modify is_running method to include server limit enforcement
def is_running(self, agent_id: str) -> bool:
    if not enforce_server_limits_for_running(agent_id):
        return False
    
    # Existing code for checking if an agent is running
    proc = self._processes.get(agent_id)
    return proc is not None and proc.poll() is None