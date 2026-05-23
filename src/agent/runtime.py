class RuntimeState:
    def __init__(self, state: str = "idle", task_id: str = None):
        self.state = state
        self.task_id = task_id

class AgentRuntime:
    def __init__(self):
        self.states = {}
        self.running_tasks = {}

    async def start(self, agent_id: str, command: list, env: Optional[Dict] = None) -> bool:
        if agent_id in self.running_tasks:
            return False  # Agent is already running
        self.states[agent_id] = RuntimeState(state="running")
        self.running_tasks[agent_id] = {"command": command, "env": env}
        return True

    async def stop(self, agent_id: str, timeout: int = 10) -> bool:
        if agent_id not in self.running_tasks:
            return False  # Agent is not running
        await asyncio.sleep(timeout)
        self.states[agent_id] = RuntimeState(state="stopped")
        del self.running_tasks[agent_id]
        return True

    def get_state(self, agent_id: str) -> RuntimeState:
        return self.states.get(agent_id, None)

    def is_running(self, agent_id: str) -> bool:
        return agent_id in self.running_tasks

    async def _run_agent_task(self, task_id: str):
        if task_id not in self.running_tasks:
            return False  # Task is not running
        # Simulate task execution
        await asyncio.sleep(5)
        self.states[task_id] = RuntimeState(state="completed")
        del self.running_tasks[task_id]
        return True

    async def _handle_event(self, event_type: str):
        if event_type in ["START", "STOP"]:
            if event_type == "START":
                await self._run_agent_task(event_type)
            else:
                await self.stop(event_type)

async def main():
    runtime = AgentRuntime()
    await runtime.start("agent1", ["start"], {"key": "value"})
    await runtime._handle_event("START")
    print(runtime.get_state("agent1"))

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())