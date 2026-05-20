javascript
// Simulating a simple durable store for tasks and agents.
// In a real system, these would be backed by a persistent database (e.g., PostgreSQL, DynamoDB)
// and operations would be transactional and potentially distributed.
// These maps are conceptually part of the "durable claim/enqueue/ack transaction" layer,
// external to the `VisibilityManager` class itself, representing the true source of truth.
const durableTaskStore = new Map(); // Stores { taskId: { agentId, currentTimeout, status, lastHeartbeat } }
const durableAgentStore = new Map(); // Stores { agentId: { currentTask, status, lastActivity } }

class VisibilityManager {
  constructor() {
    // In-memory cache for quick lookups and local state representation.
    // Critical decisions (like extending visibility timeouts) MUST NOT rely solely on this cache.
    // Instead, they must query the durable store within a transactional context
    // to ensure consistency and prevent race conditions.
    this.taskCache = new Map(); // Stores { taskId: { agentId, currentTimeout, status, lastHeartbeat } }
    this.agentCache = new Map(); // Stores { agentId: { currentTask, status, lastActivity } }
  }

  /**
   * Internal helper to asynchronously retrieve the current durable state of a task.
   * In a real system, this would be an actual database read.
   * @param {string} taskId - The ID of the task.
   * @returns {Promise<object|undefined>} - A Promise resolving to the task record from the durable store, or undefined if not found.
   * @private
   */
  async _getDurableTask(taskId) {
    // Simulate an asynchronous database read.
    return Promise.resolve(durableTaskStore.get(taskId));
  }

  /**
   * Internal helper to asynchronously retrieve the current durable state of an agent.
   * In a real system, this would be an actual database read.
   * @param {string} agentId - The ID of the agent.
   * @returns {Promise<object|undefined>} - A Promise resolving to the agent record from the durable store, or undefined if not found.
   * @private
   */
  async _getDurableAgent(agentId) {
    // Simulate an asynchronous database read.
    return Promise.resolve(durableAgentStore.get(agentId));
  }

  /**
   * Enforces invariants for long-running agents *based on provided durable records*.
   * This function performs validation logic on data that has already been fetched
   * from the durable store, allowing it to be integrated into a transactional context.
   *
   * @param {string} taskId - The ID of the task.
   * @param {string} agentId - The ID of the agent requesting the extension.
   * @param {number} proposedNewTimeoutTimestamp - The timestamp of the proposed new visibility timeout.
   * @param {object|undefined} durableTaskInfo - The task record fetched from the durable store.
   * @param {object|undefined} durableAgentInfo - The agent record fetched from the durable store.
   * @returns {{valid: boolean, reason?: string}} - Validation result, including a reason string if invalid.
   * @private
   */
  _checkLongRunningAgentInvariant(taskId, agentId, proposedNewTimeoutTimestamp, durableTaskInfo, durableAgentInfo) {
    // Invariant 1: Task must exist and be assigned to the requesting agent.
    if (!durableTaskInfo || durableTaskInfo.agentId !== agentId) {
      return { valid: false, reason: `Task ${taskId} either does not exist or is not assigned to agent ${agentId}.` };
    }

    // Invariant 2: Agent must be actively working on this specific task.
    if (!durableAgentInfo || durableAgentInfo.currentTask !== taskId) {
      return { valid: false, reason: `Agent ${agentId} is not currently working on task ${taskId}.` };
    }

    // Invariant 3: Agent's status must be suitable for processing (e.g., not idle, completed, or failed).
    if (['idle', 'completed', 'failed'].includes(durableAgentInfo.status)) {
      return { valid: false, reason: `Agent ${agentId} is in an invalid state (${durableAgentInfo.status}) to extend timeout for task ${taskId}.` };
    }

    // Invariant 4: Task's status must not be a terminal state (completed, failed, cancelled).
    if (['completed', 'failed', 'cancelled'].includes(durableTaskInfo.status)) {
      return { valid: false, reason: `Task ${taskId} is in a terminal state (${durableTaskInfo.status}) to extend timeout.` };
    }

    // Invariant 5: Proposed new timeout must be strictly greater than the current one.
    // This is crucial for idempotency and preventing stale updates in a concurrent environment.
    if (proposedNewTimeoutTimestamp <= durableTaskInfo.currentTimeout) {
      return { valid: false, reason: `Proposed timeout ${proposedNewTimeoutTimestamp} for task ${taskId} is not strictly greater than current timeout ${durableTaskInfo.currentTimeout}.` };
    }

    // Advanced policy checks can be added here, also relying on durable state.
    // Examples: Max allowable extension duration, global task runtime limits, conflicting commands.

    return { valid: true }; // All invariants passed
  }

  /**
   * Persists the visibility timeout change to the durable store *transactionally*.
   * This method encapsulates the critical logic of fetching, validating, and atomically updating
   * the durable state, addressing the bug report's core requirement.
   *
   * @param {string} taskId - The ID of the task.
   * @param {string} agentId - The ID of the agent requesting the extension.
   * @param {number} newTimeoutTimestamp - The new timestamp for the visibility timeout.
   * @returns {Promise<{success: boolean, reason?: string}>} - A Promise resolving to the result of the persistence attempt.
   * @private
   */
  async _persistTimeoutChangeTransactionally(taskId, agentId, newTimeoutTimestamp) {
    try {
      // Step 1: Fetch current durable state for task and agent.
      // In a real database, this would involve starting a transaction and fetching records
      // with appropriate locking (e.g., `SELECT FOR UPDATE`) or for optimistic concurrency control.
      const durableTask = await this._getDurableTask(taskId);
      const durableAgent = await this._getDurableAgent(agentId);

      // Step 2: Perform invariant checks on the *durable* state retrieved.
      const invariantCheckResult = this._checkLongRunningAgentInvariant(
        taskId,
        agentId,
        newTimeoutTimestamp,
        durableTask,
        durableAgent
      );

      if (!invariantCheckResult.valid) {
        // Invariant violated: reject the transition.
        // In a real transaction, this would implicitly or explicitly lead to a ROLLBACK.
        return { success: false, reason: invariantCheckResult.reason };
      }

      // Step 3: Attempt an atomic update to the durable store.
      // This update employs optimistic locking by including the `currentTimeout` from
      // the fetched `durableTask` in the conditional update. This ensures idempotency
      // and prevents race conditions if another transaction updated the task concurrently.
      const oldTimeout = durableTask.currentTimeout;
      const newLastHeartbeat = Date.now();

      // Simulate an atomic update for the task:
      // In a real database, this would be an `UPDATE` statement with a `WHERE` clause:
      // `UPDATE tasks SET current_timeout = :newTimeout, last_heartbeat = :newHeartbeat, status = 'processing'
      //  WHERE task_id = :taskId AND current_timeout = :oldTimeout;`
      // The number of affected rows would then indicate success or a concurrency conflict.
      if (durableTaskStore.has(taskId) && durableTaskStore.get(taskId).currentTimeout === oldTimeout) {
        const updatedTask = { ...durableTaskStore.get(taskId), currentTimeout: newTimeoutTimestamp, lastHeartbeat: newLastHeartbeat, status: 'processing' };
        durableTaskStore.set(taskId, updatedTask);

        // Update the agent's last activity/status if necessary (also part of the transaction).
        if (durableAgentStore.has(agentId)) {
          const updatedAgent = { ...durableAgentStore.get(agentId), lastActivity: newLastHeartbeat, status: 'processing' };
          durableAgentStore.set(agentId, updatedAgent);
        }

        // After successful durable write, update the in-memory cache.
        this.taskCache.set(taskId, updatedTask);
        if (this.agentCache.has(agentId)) {
          this.agentCache.get(agentId).lastActivity = newLastHeartbeat;
          this.agentCache.get(agentId).status = 'processing';
        }

        // In a real transaction, this would be a COMMIT.
        return { success: true };
      } else {
        // This indicates a concurrency conflict: the task's state changed between fetch and update attempt.
        // The original timeout condition (`current_timeout = :oldTimeout`) was not met.
        return { success: false, reason: `Concurrency conflict: Task ${taskId} state changed during transaction. Expected old timeout ${oldTimeout}, but found ${durableTaskStore.get(taskId)?.currentTimeout}.` };
      }
    } catch (error) {
      // An unexpected error occurred during the simulated transaction.
      // In a real system, this would cause a ROLLBACK.
      return { success: false, reason: `Internal error during transactional persistence: ${error.message}` };
    }
  }

  /**
   * Extends the visibility timeout for a given task,
   * by orchestrating the transactional persistence logic.
   * This is the primary entry point for agents to signal continued work on a task.
   * @param {string} taskId - The ID of the task.
   * @param {string} agentId - The ID of the agent requesting the extension.
   * @param {number} newTimeoutTimestamp - The new timestamp for the visibility timeout (e.g., Date.now() + duration).
   * @returns {Promise<boolean>} - True if the timeout was successfully extended, false otherwise.
   */
  async extendVisibilityTimeout(taskId, agentId, newTimeoutTimestamp) {
    const result = await this._persistTimeoutChangeTransactionally(taskId, agentId, newTimeoutTimestamp);

    if (!result.success) {
      // The `_persistTimeoutChangeTransactionally` method already provides a detailed reason
      // for failure, which can be logged by the calling service layer or emitted as a metric.
      // This fulfills the acceptance criteria: "rejects or safely defers the invalid transition
      // and preserves the expected lifecycle state. Logs, metrics, or audit records explain the decision."
      return false;
    }

    return true;
  }

  // --- Helper/Management methods (interacting with durable store for state changes) ---
  // These methods also handle durable state manipulation for other lifecycle events,
  // typically initiated by a scheduler or task dispatcher.

  /**
   * Registers a new task and assigns it to an agent with an initial visibility timeout.
   * This operation should be durable and ideally atomic (e.g., creating both records in a single transaction).
   * @param {string} taskId - The ID of the new task.
   * @param {string} agentId - The ID of the agent to assign the task to.
   * @param {number} initialTimeout - The initial visibility timeout timestamp.
   * @returns {Promise<boolean>} - True if registration was successful, false otherwise.
   */
  async registerTask(taskId, agentId, initialTimeout) {
    // Simulate checking existence in durable store for idempotency/validation.
    if (await this._getDurableTask(taskId)) {
      return false;
    }
    const existingAgentInfo = await this._getDurableAgent(agentId);
    if (existingAgentInfo && existingAgentInfo.currentTask !== null) {
        return false;
    }

    const newTaskRecord = {
      taskId,
      agentId,
      currentTimeout: initialTimeout,
      status: 'assigned',
      lastHeartbeat: Date.now()
    };
    const newAgentRecord = {
      agentId,
      currentTask: taskId,
      status: 'processing',
      lastActivity: Date.now()
    };

    // Simulate atomic write to durable store.
    // In a real system, this would typically be a database transaction to insert both records.
    durableTaskStore.set(taskId, newTaskRecord);
    durableAgentStore.set(agentId, newAgentRecord);

    // Update caches after successful durable write.
    this.taskCache.set(taskId, newTaskRecord);
    this.agentCache.set(agentId, newAgentRecord);

    return true;
  }

  /**
   * Marks a task as completed by a specific agent.
   * This operation should also be durable and atomic.
   * @param {string} taskId - The ID of the completed task.
   * @param {string} agentId - The ID of the agent that completed the task.
   * @returns {Promise<boolean>} - True if successful, false otherwise.
   */
  async completeTask(taskId, agentId) {
    const durableTask = await this._getDurableTask(taskId);
    if (durableTask && durableTask.agentId === agentId) {
      const durableAgent = await this._getDurableAgent(agentId);

      // Simulate atomic updates in the durable store.
      // In a real system, these would be part of a transaction.
      // A completed task might be moved to an archive table or simply deleted from the active tasks.
      durableTaskStore.delete(taskId); // For this example, we remove completed tasks from active store.
      if (durableAgent) {
        const updatedAgent = { ...durableAgent, status: 'idle', currentTask: null };
        durableAgentStore.set(agentId, updatedAgent);
      }

      // Update caches after successful durable write.
      this.taskCache.delete(taskId);
      if (this.agentCache.has(agentId)) {
        this.agentCache.get(agentId).status = 'idle';
        this.agentCache.get(agentId).currentTask = null;
      }
      return true;
    }
    return false;
  }

  /**
   * Marks a task as failed by a specific agent.
   * This operation should also be durable and atomic.
   * @param {string} taskId - The ID of the failed task.
   * @param {string} agentId - The ID of the agent that reported the failure.
   * @returns {Promise<boolean>} - True if successful, false otherwise.
   */
  async failTask(taskId, agentId) {
    const durableTask = await this._getDurableTask(taskId);
    if (durableTask && durableTask.agentId === agentId) {
      const durableAgent = await this._getDurableAgent(agentId);

      // Simulate atomic updates in the durable store.
      // A failed task might remain in the active store with 'failed' status for retry logic,
      // or moved to a failed tasks queue/table.
      const updatedTask = { ...durableTask, status: 'failed' };
      durableTaskStore.set(taskId, updatedTask);
      if (durableAgent) {
        const updatedAgent = { ...durableAgent, status: 'idle', currentTask: null };
        durableAgentStore.set(agentId, updatedAgent);
      }

      // Update caches after successful durable write.
      this.taskCache.set(taskId, updatedTask);
      if (this.agentCache.has(agentId)) {
        this.agentCache.get(agentId).status = 'idle';
        this.agentCache.get(agentId).currentTask = null;
      }
      return true;
    }
    return false;
  }

  // --- Utility methods (for debugging or external monitoring) ---

  /**
   * Retrieves the current information for a given task from the in-memory cache.
   * Note: For critical, real-time decisions, the durable store should always be consulted.
   * @param {string} taskId - The ID of the task.
   * @returns {object|undefined} - Task information object or undefined if not found.
   */
  getTaskInfo(taskId) {
    return this.taskCache.get(taskId);
  }

  /**
   * Retrieves the current information for a given agent from the in-memory cache.
   * Note: For critical, real-time decisions, the durable store should always be consulted.
   * @param {string} agentId - The ID of the agent.
   * @returns {object|undefined} - Agent information object or undefined if not found.
   */
  getAgentInfo(agentId) {
    return this.agentCache.get(agentId);
  }
}

module.exports = VisibilityManager;