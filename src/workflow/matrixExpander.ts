typescript
/**
 * Represents the data associated with a state transition or a potential matrix expansion item.
 */
interface TransitionData {
  id: string;
  type: 'agent_run' | 'task' | 'handler';
  currentState: string;
  targetState: string;
  /**
   * Represents the number of new items (e.g., child tasks) to be fanned out by this transition,
   * or the total number of items in an aggregate expansion plan.
   * A value of 0 or undefined indicates no dynamic fan-out from this specific item.
   */
  fanOutCount?: number;
  /**
   * A mechanism to detect stale or duplicate transitions.
   * Could be a monotonically increasing version number, a timestamp, or a unique correlation ID.
   */
  version?: number;
  timestamp?: number;
  // Any other relevant data for the workflow item
  [key: string]: any;
}

/**
 * Defines the policies and limits for matrix expansion and workflow item counts.
 */
interface ExpansionPolicy {
  maxFanOutLimit: number; // Maximum number of direct child items allowed from a single dynamic fan-out.
  maxTotalWorkflowItems: number; // Overall limit for the total number of active items in the workflow.
  // Add other policy definitions as needed (e.g., rate limits, resource limits per item)
}

/**
 * Represents the current status of the workflow, tracking all active items.
 */
interface WorkflowStatus {
  items: { [id: string]: TransitionData }; // Map task/agent IDs to their current state data
  totalActiveItems: number; // Count of all active tasks/agents/handlers in the 'items' map.
}

/**
 * Manages the expansion of workflow matrices and applies state transitions
 * for agent runs, tasks, and handlers, enforcing configured limits and invariants.
 *
 * NOTE: This class manages an in-memory workflow state. In a highly concurrent,
 * distributed environment, external mechanisms such as optimistic locking (leveraging
 * the `version` field), distributed locks, or a transactional persistent store
 * would be necessary to ensure data consistency and prevent race conditions.
 * For simplicity, explicit internal concurrency control mechanisms are not implemented.
 */
class WorkflowMatrixExpander {
  private workflowStatus: WorkflowStatus;
  private expansionPolicy: ExpansionPolicy;

  constructor(initialStatus: WorkflowStatus = { items: {}, totalActiveItems: 0 }, policy: ExpansionPolicy) {
    // Ensure policy limits are valid and sensible.
    if (policy.maxFanOutLimit <= 0 || policy.maxTotalWorkflowItems <= 0) {
      throw new Error('ExpansionPolicy limits (maxFanOutLimit, maxTotalWorkflowItems) must be positive numbers.');
    }
    if (initialStatus.totalActiveItems < 0) {
      throw new Error('Initial workflowStatus.totalActiveItems cannot be negative.');
    }
    this.workflowStatus = initialStatus;
    this.expansionPolicy = policy;
  }

  /**
   * Internal method to enforce the dynamic fan-out invariant and other expansion limits.
   * This is the core of the bug fix: ensuring validation BEFORE any state commitment.
   *
   * @param potentialTransition The transition data that is about to be applied or expanded.
   * @param isAggregateExpansion A flag indicating if this validation is for an an aggregate expansion plan.
   *                             If true, `potentialTransition.fanOutCount` is treated as the number of *new unique items*
   *                             expected from the aggregate expansion.
   *                             If false, `newItemsCount` is 1 if `potentialTransition.id` is new, otherwise 0.
   * @throws Error if any expansion limits are violated.
   */
  private _validateExpansionLimits(potentialTransition: TransitionData, isAggregateExpansion: boolean = false): void {
    // 1. Check for policy-violating fan-out limits for this specific transition/expansion.
    // This applies to a direct fan-out request from a single item.
    // For aggregate expansion, `potentialTransition.fanOutCount` represents the total number of *new* items in the batch.
    if (!isAggregateExpansion && potentialTransition.fanOutCount !== undefined) {
      if (potentialTransition.fanOutCount < 0) {
        throw new Error('Matrix expansion limits violated: Individual item fanOutCount cannot be negative.');
      }
      if (potentialTransition.fanOutCount > this.expansionPolicy.maxFanOutLimit) {
        throw new Error(
          `Matrix expansion limits violated: Dynamic fan-out count (${potentialTransition.fanOutCount}) for item '${potentialTransition.id}' exceeds policy limit (${this.expansionPolicy.maxFanOutLimit}).`
        );
      }
    }

    // 2. Check for overall workflow item limit.
    // Calculate the number of new unique items this operation would add to `totalActiveItems`.
    let newItemsCount = 0;
    if (isAggregateExpansion) {
      // If it's an aggregate check, `fanOutCount` represents the total new unique items to be added.
      // Ensure fanOutCount is non-negative for aggregate validation.
      if (potentialTransition.fanOutCount === undefined || potentialTransition.fanOutCount < 0) {
        throw new Error('Matrix expansion limits violated: Aggregate fanOutCount must be a non-negative number when isAggregateExpansion is true.');
      }
      newItemsCount = potentialTransition.fanOutCount;
    } else if (!(potentialTransition.id in this.workflowStatus.items)) {
      // If it's a single item transition and the item ID is new to the workflow, it adds 1 to the total count.
      newItemsCount = 1;
    }
    // If it's an update to an existing item, newItemsCount remains 0.

    const estimatedNewTotalItems = this.workflowStatus.totalActiveItems + newItemsCount;
    if (estimatedNewTotalItems > this.expansionPolicy.maxTotalWorkflowItems) {
      throw new Error(
        `Matrix expansion limits violated: Total workflow items (${estimatedNewTotalItems}) would exceed policy limit (${this.expansionPolicy.maxTotalWorkflowItems}).`
      );
    }

    // 3. Check for stale or duplicate transitions, primarily for single item transitions.
    // This is skipped for aggregate validation as it's a temporary, synthetic transition for count,
    // and for items that are being newly generated as part of an expansion (they shouldn't exist yet).
    if (!isAggregateExpansion) {
      const existingState = this.workflowStatus.items[potentialTransition.id];
      if (existingState) {
        // Detect stale transition by version number.
        // An incoming transition's version must be strictly greater than the current.
        if (potentialTransition.version !== undefined && existingState.version !== undefined) {
          if (potentialTransition.version <= existingState.version) {
            throw new Error(
              `Matrix expansion limits violated: Stale transition detected for ID '${potentialTransition.id}'. Incoming version (${potentialTransition.version}) is not greater than current version (${existingState.version}).`
            );
          }
        }
        // Detect exact duplicate based on key properties (e.g., targetState)
        // This check aims to prevent redundant operations if the item is already in the requested state.
        if (existingState.targetState === potentialTransition.targetState &&
            existingState.currentState === potentialTransition.currentState &&
            existingState.type === potentialTransition.type &&
            (potentialTransition.version === existingState.version || potentialTransition.timestamp === existingState.timestamp)
        ) {
          throw new Error(
            `Matrix expansion limits violated: Duplicate transition detected for ID '${potentialTransition.id}' to state '${potentialTransition.targetState}'. The item is already in this state with identical version/timestamp.`
          );
        }
      }
    }
    // Further specific policy checks can be added here as per project requirements.
  }

  /**
   * Applies a state transition for an agent run, task, or handler.
   * This method now enforces validation BEFORE committing any state changes,
   * ensuring the dynamic fan-out invariant is maintained.
   *
   * @param transitionData The data describing the state change.
   * @returns The updated TransitionData after committing.
   * @throws Error if the transition violates any expansion limits or policies.
   */
  public applyStateTransition(transitionData: TransitionData): TransitionData {
    // Core fix: Enforce the dynamic fan-out invariant BEFORE committing any state.
    // The `isAggregateExpansion` flag is false here, as this is a single item transition.
    this._validateExpansionLimits(transitionData, false);

    // If validation passes, proceed to commit the state.
    // Check if this is a new item being added to the workflow.
    const isNewItem = !(transitionData.id in this.workflowStatus.items);

    if (isNewItem) {
      this.workflowStatus.totalActiveItems += 1;
    }

    // Update the state for the specific item.
    // Create a shallow copy to prevent external mutation of the internal state object.
    this.workflowStatus.items[transitionData.id] = { ...transitionData };

    // In a real system, this is where interactions with persistent storage,
    // message queues, scheduling services, routing services, or other
    // workflow components would occur to commit the changes.
    // For this in-memory mock, the internal state update signifies "commitment."

    return this.workflowStatus.items[transitionData.id];
  }

  /**
   * Expands a workflow matrix based on a given definition and context.
   * This method ensures that the *cumulative effect* of the expansion
   * adheres to the configured limits BEFORE any new items are created or committed.
   * It also performs provisional validation for each individual item, ensuring
   * an "all-or-nothing" semantic for batch expansion.
   *
   * @param workflowDefinition The definition guiding the matrix expansion.
   * @param currentContext The current execution context for the expansion.
   * @returns An array of TransitionData for the newly created or updated items.
   * @throws Error if the expansion violates any limits or policies, or if any individual
   *              generated item fails provisional validation (e.g., stale version, duplicate).
   */
  public expandMatrix(workflowDefinition: any, currentContext: any): TransitionData[] {
    const potentialExpansionItems: TransitionData[] = this._calculatePotentialExpansion(workflowDefinition, currentContext);

    // 1. Calculate the number of truly new items that would be added by this expansion
    // to correctly assess the impact on `maxTotalWorkflowItems`.
    const newItemsAddedByExpansion = potentialExpansionItems.filter(item => !(item.id in this.workflowStatus.items)).length;

    // 2. Validate aggregate limits for the entire expansion batch.
    // This ensures that the dynamic fan-out invariant (maxTotalWorkflowItems)
    // is enforced *before* any new scheduling, routing, or workflow states are committed.
    const aggregateTransition: TransitionData = {
      id: `aggregate-expansion-${Date.now()}`, // Temporary ID for aggregate validation
      type: 'task', // Generic type for aggregate checks
      currentState: 'expanding',
      targetState: 'expanded',
      fanOutCount: newItemsAddedByExpansion, // Total count of *new* items for aggregate validation
    };
    this._validateExpansionLimits(aggregateTransition, true);

    // 3. Perform provisional individual validation for each item within the batch.
    // This is crucial to ensure that if *any* single item within the batch is invalid
    // (e.g., stale version, duplicate relative to the *current* workflow state),
    // the *entire batch expansion is rejected* BEFORE any state changes are committed.
    // This enforces an "all-or-nothing" transactional behavior for batch expansions.
    for (const item of potentialExpansionItems) {
      try {
        // Call validation with a shallow copy to prevent any side effects on the original item.
        this._validateExpansionLimits({ ...item }, false);
      } catch (error: any) {
        // If an individual item fails provisional validation, the entire expansion fails.
        throw new Error(`Matrix expansion aborted: Provisional validation failed for item '${item.id}': ${error.message}`);
      }
    }

    // 4. If all aggregate and individual provisional validations pass, then commit all items.
    // Each individual item's state transition will also be validated by `applyStateTransition`,
    // acting as a final safeguard, though these checks should now pass given prior provisional validation.
    const expandedItems: TransitionData[] = [];
    for (const item of potentialExpansionItems) {
      // `applyStateTransition` includes its own `_validateExpansionLimits` call,
      // which will re-verify the item before committing.
      const committedItem = this.applyStateTransition(item);
      expandedItems.push(committedItem);
    }

    return expandedItems;
  }

  /**
   * Helper method to simulate calculating potential expansion based on a workflow definition.
   * In a real system, this would involve complex logic to interpret the workflow
   * definition and current execution context to determine the items to be fanned out.
   * For the purpose of this example, it generates a specified number of new tasks.
   */
  private _calculatePotentialExpansion(workflowDefinition: any, currentContext: any): TransitionData[] {
    const numItemsToFanOut = workflowDefinition.fanOut || 1;

    // Defensive check to prevent attempting to simulate an excessively large expansion,
    // which could lead to out-of-memory errors even before limits are validated.
    // This limit is heuristic and could be made configurable.
    const reasonableMaxSimulation = this.expansionPolicy.maxTotalWorkflowItems * 2; // e.g., twice the total allowed
    if (numItemsToFanOut > reasonableMaxSimulation) {
      throw new Error(
        `Simulation of potential expansion aborted: Requested fan-out count (${numItemsToFanOut}) is excessively large for calculation purposes.`
      );
    }
    if (numItemsToFanOut < 0) {
      throw new Error('Simulation of potential expansion aborted: Requested fan-out count cannot be negative.');
    }

    const baseId = `task-${currentContext.workflowId || 'default'}`;
    const items: TransitionData[] = [];

    for (let i = 0; i < numItemsToFanOut; i++) {
      // Use Date.now() + i to ensure unique IDs within a rapid loop execution.
      const itemId = `${baseId}-${i}-${Date.now() + i}`;
      items.push({
        id: itemId,
        type: 'task',
        currentState: 'pending',
        targetState: 'scheduled',
        fanOutCount: 0, // Individual items created by expansion typically don't fan out further initially
        version: 1, // Start with version 1
        timestamp: Date.now(),
        // ... other task-specific data
      });
    }

    return items;
  }

  /**
   * Retrieves a copy of the current workflow status.
   * @returns A deep copy of the current WorkflowStatus (shallow copy of `items` map).
   */
  public getWorkflowStatus(): WorkflowStatus {
    // Return a defensive copy to prevent external direct modification of internal state.
    return {
      totalActiveItems: this.workflowStatus.totalActiveItems,
      items: { ...this.workflowStatus.items } // Creates a shallow copy of the `items` map itself.
    };
  }
}