typescript
/**
 * Interface for a logging utility.
 * Adheres to the acceptance criteria for logging without exposing private data.
 */
interface Logger {
    debug(message: string, ...args: any[]): void;
    info(message: string, ...args: any[]): void;
    warn(message: string, ...args: any[]): void;
    error(message: string, ...args: any[]): void;
}

/**
 * A default no-operation logger.
 * This ensures no console.log statements are left in production builds
 * if a specific logger isn't provided.
 */
class NoopLogger implements Logger {
    debug(..._args: any[]): void {}
    info(..._args: any[]): void {}
    warn(..._args: any[]): void {}
    error(..._args: any[]): void {}
}

/**
 * Manages and limits the capacity for a specific queue or resource within a single process.
 * This implementation tracks capacity based on unique job identifiers, enabling idempotent
 * capacity acquisition and release. It guarantees that temporary capacity is released
 * whether the wrapped operation succeeds or fails, preventing capacity leaks
 * during transactional rollbacks.
 */
class QueueCapacityManager {
    private static instance: QueueCapacityManager;
    private maxCapacity: number;
    // Tracks unique job IDs currently occupying capacity.
    // This allows for idempotent acquire/release operations and accurate capacity counting.
    private occupiedJobs: Set<string>;
    private logger: Logger;

    /**
     * Private constructor to enforce Singleton pattern.
     * @param maxCapacity The maximum number of items the queue can hold.
     * @param logger Optional. A logger instance for internal reporting. Defaults to a no-op logger.
     */
    private constructor(maxCapacity: number, logger?: Logger) {
        if (maxCapacity <= 0) {
            throw new Error("Max capacity must be a positive number.");
        }
        this.maxCapacity = maxCapacity;
        this.occupiedJobs = new Set<string>();
        this.logger = logger || new NoopLogger();
        this.logger.info(`QueueCapacityManager initialized with max capacity: ${maxCapacity}`);
    }

    /**
     * Gets the singleton instance of QueueCapacityManager.
     * @param maxCapacity Optional. Must be provided on the first call to initialize the manager.
     * @param logger Optional. A logger instance to be used by the manager.
     * @returns The singleton instance.
     * @throws Error if maxCapacity is not provided on first instantiation.
     */
    public static getInstance(maxCapacity?: number, logger?: Logger): QueueCapacityManager {
        if (!QueueCapacityManager.instance) {
            if (maxCapacity === undefined) {
                throw new Error("QueueCapacityManager: Max capacity must be provided on first instantiation.");
            }
            QueueCapacityManager.instance = new QueueCapacityManager(maxCapacity, logger);
        } else if (logger && QueueCapacityManager.instance.logger instanceof NoopLogger) {
            // Allow updating the logger if it was initially a no-op logger
            QueueCapacityManager.instance.logger = logger;
            QueueCapacityManager.instance.logger.info("QueueCapacityManager logger updated.");
        }
        return QueueCapacityManager.instance;
    }

    /**
     * Attempts to acquire one unit of capacity for a given job ID.
     * This operation is idempotent: if the job ID already occupies capacity,
     * it will still return true without acquiring additional capacity.
     *
     * @param jobId A unique identifier for the job attempting to acquire capacity.
     * @returns True if capacity was successfully acquired or already held by this jobId, false if the limit is reached.
     */
    public acquireCapacity(jobId: string): boolean {
        if (this.occupiedJobs.has(jobId)) {
            this.logger.debug(`Capacity already held for job: ${jobId}. Idempotent acquisition.`);
            return true; // Idempotent: capacity already acquired for this job.
        }

        if (this.occupiedJobs.size < this.maxCapacity) {
            this.occupiedJobs.add(jobId);
            this.logger.info(`Capacity acquired for job: ${jobId}. Current occupied: ${this.occupiedJobs.size}/${this.maxCapacity}`);
            return true;
        }

        this.logger.warn(`Capacity limit reached for job: ${jobId}. Occupied: ${this.occupiedJobs.size}/${this.maxCapacity}`);
        return false;
    }

    /**
     * Releases one unit of capacity for a given job ID.
     * This operation is idempotent: attempting to release capacity for a job ID
     * that does not currently hold capacity will result in no change.
     *
     * @param jobId A unique identifier for the job whose capacity is being released.
     */
    public releaseCapacity(jobId: string): void {
        if (this.occupiedJobs.delete(jobId)) {
            this.logger.info(`Capacity released for job: ${jobId}. Current occupied: ${this.occupiedJobs.size}/${this.maxCapacity}`);
        } else {
            this.logger.debug(`Attempted to release capacity for job: ${jobId}, but it was not found or already released.`);
        }
    }

    /**
     * Executes an asynchronous operation that requires temporary capacity.
     * Guarantees that capacity is released once the operation completes (succeeds or fails),
     * preventing capacity leaks, especially during transactional rollbacks.
     *
     * This method fulfills the core bug fix: capacity is released when the wrapped
     * durable claim/enqueue/ack transaction fails or rolls back. It also assumes
     * that if the transaction succeeds, the job's capacity is now managed by the
     * durable queue system itself, so the temporary capacity held by this manager
     * can also be released.
     *
     * @template T The return type of the operation.
     * @param jobId A unique identifier for the job associated with this operation. Crucial for idempotency and tracking.
     * @param operation An asynchronous function that performs the core logic (e.g., enqueue, DB transaction).
     * @returns A Promise that resolves with the result of the operation.
     * @throws Error if capacity cannot be acquired or if the operation itself throws an error.
     */
    public async executeWithCapacity<T>(jobId: string, operation: () => Promise<T>): Promise<T> {
        this.logger.debug(`[${jobId}] Attempting to execute operation with capacity.`);

        if (!this.acquireCapacity(jobId)) {
            const errorMessage = `[${jobId}] Queue capacity limit reached. Cannot process new items. Occupied: ${this.occupiedJobs.size}/${this.maxCapacity}`;
            this.logger.error(errorMessage);
            throw new Error(errorMessage);
        }

        try {
            this.logger.debug(`[${jobId}] Capacity acquired. Executing operation.`);
            const result = await operation();
            this.logger.info(`[${jobId}] Operation completed successfully.`);
            return result;
        } catch (error) {
            this.logger.error(`[${jobId}] Operation failed. Releasing capacity due to potential rollback. Error: ${error instanceof Error ? error.message : String(error)}`, error);
            throw error; // Re-throw the original error for upstream handling.
        } finally {
            // CORE FIX: Ensure capacity is always released regardless of operation success or failure.
            // This prevents capacity leaks if the transaction fails or if the capacity is only
            // temporarily held during the initial processing/enqueuing phase.
            this.logger.debug(`[${jobId}] Finally block: Releasing temporary capacity.`);
            this.releaseCapacity(jobId);
        }
    }

    /**
     * Returns the number of currently occupied capacity units (i.e., unique jobs).
     */
    public getCurrentOccupiedCount(): number {
        return this.occupiedJobs.size;
    }

    /**
     * Returns the maximum allowed capacity.
     */
    public getMaxCapacity(): number {
        return this.maxCapacity;
    }

    /**
     * Resets the manager's state, clearing all occupied capacity.
     * Useful primarily for testing or explicit system resets.
     */
    public reset(): void {
        this.occupiedJobs.clear();
        this.logger.warn("QueueCapacityManager state has been reset.");
        // For singleton, also reset the instance to allow re-initialization with new maxCapacity if needed
        (QueueCapacityManager.instance as any) = null;
    }
}