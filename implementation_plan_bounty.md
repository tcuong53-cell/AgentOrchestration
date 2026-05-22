# Implementation Plan

## Root Cause Analysis

The bug arises from the lack of explicit state-machine guard before emitting side effects during the result runtime path. This allows for inconsistent run states under load, leading to potential issues such as duplicate work, lost state, exhausted worker resources, or inaccurate execution outcomes.

## Planned Modifications

1. **Add Explicit State-Machine Guard**: Before emitting any side effects (e.g., dispatching work, recording success), add a guard that checks the validity of JSON serialization of tool results. This ensures that the data is correctly formatted before proceeding with further operations.

2. **Persist Durable State**: Ensure that all state changes are persisted durably before emitting side effects. This prevents any potential loss of state in case of failures or retries.

3. **Make Retries Bounded and Idempotent**: Implement retry logic that limits the number of attempts and ensures idempotency, preventing duplicate work from being executed.

4. **Cover Affected State Transition**: Specifically cover the `Validate JSON serialization of tool results` condition in the `result runtime` to ensure future changes do not reintroduce the gap.

5. **Unit or Integration Tests**: Write unit or integration tests that cover the affected state transition under retry, cancellation, or concurrency scenarios. This ensures that the fix works as expected and prevents regressions.

6. **Worker Records One Durable Terminal Outcome**: Ensure that the worker records one durable terminal outcome and does not leave orphaned work or stale locks. This maintains consistency in run states and prevents resource exhaustion.

### Target File to Modify

`src/agent/runtime.py`

### Implementation Steps

1. **Identify the State Transition**: Locate the state transition path in `runtime.py` that involves emitting side effects during the result runtime.

2. **Add Guard for JSON Serialization Validation**: Before emitting any side effects, add a check to validate the JSON serialization of tool results using a library like `jsonschema`. If validation fails, return an error or log an issue before proceeding with further operations.

3. **Persist Durable State**: Ensure that all state changes are persisted durably before emitting side effects. This can be achieved by using a database or a file system to store the state.

4. **Implement Retry Logic**: Add retry logic that limits the number of attempts and ensures idempotency. Use a library like `tenacity` to handle retries with exponential backoff.

5. **Update Unit Tests**: Write unit tests that cover the affected state transition under retry, cancellation, or concurrency scenarios. This can be done using frameworks like `unittest` or `pytest`.

6. **Test Worker Behavior**: Ensure that the worker records one durable terminal outcome and does not leave orphaned work or stale locks. This can be tested by running the agent in a controlled environment and verifying the state transitions.

7. **Submit Proof of Fix**: Provide proof of the fix, such as test output, screenshots, or a demo video, to demonstrate that the bug has been resolved.

By following these steps, we can ensure that the bug is fixed and that the system behaves as expected under various conditions.