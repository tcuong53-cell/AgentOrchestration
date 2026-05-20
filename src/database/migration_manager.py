import os
import sys
import time
import datetime
import sqlite3
import logging
from contextlib import contextmanager

# Configure logging
# Using INFO level for operational logs, WARNING for non-critical issues, ERROR/CRITICAL for failures.
logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                    format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Configuration - These would typically come from environment variables or a config file
DB_PATH = os.environ.get('DATABASE_PATH', 'database.db')
MIGRATIONS_DIR = os.path.join(os.path.dirname(__file__), 'migrations')
LOCK_TIMEOUT_SECONDS = 300  # 5 minutes to acquire a lock
RETRY_INTERVAL_SECONDS = 5
CALLER_ID = os.environ.get('MIGRATION_CALLER_ID', 'default_migration_runner')

class MigrationError(Exception):
    """Custom exception for migration-related errors."""
    pass

def get_db_connection():
    """
    Establishes and returns a database connection.

    For production, replace sqlite3.connect with the appropriate database driver
    (e.g., psycopg2 for PostgreSQL, mysql.connector for MySQL).
    Ensure proper connection pooling and error handling for production use cases.
    """
    try:
        # SQLite's default isolation level is DEFERRED. We'll manage transactions explicitly
        # using BEGIN/COMMIT/ROLLBACK statements.
        conn = sqlite3.connect(DB_PATH)
        logger.debug(f"Successfully connected to database at {DB_PATH}")
        return conn
    except Exception as e:
        raise MigrationError(f"Failed to connect to database at {DB_PATH}: {e}")

def initialize_migration_tables(conn):
    """
    Ensures migration-related tables exist in the database.
    This function must be called exactly once at the start of the migration process
    before any lock acquisition or migration checks.
    """
    cursor = conn.cursor()
    try:
        # Start a transaction for schema changes to ensure atomicity
        cursor.execute("BEGIN;") 
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS applied_migrations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS migration_lock (
                id INTEGER PRIMARY KEY DEFAULT 1,
                is_locked BOOLEAN NOT NULL DEFAULT FALSE,
                locked_by TEXT,
                locked_at TIMESTAMP
            );
        """)
        # Ensure there's always a single row for the lock table.
        # This is critical for the `acquire_migration_lock` logic.
        cursor.execute("""
            INSERT OR IGNORE INTO migration_lock (id, is_locked) VALUES (1, FALSE);
        """)
        conn.commit()
        logger.info("Migration tables initialized successfully.")
    except Exception as e:
        # Check if a transaction is active before rolling back (sqlite3 specific)
        if conn.in_transaction: 
            conn.rollback()
        raise MigrationError(f"Failed to initialize migration tables: {e}")


@contextmanager
def acquire_migration_lock(conn, caller_id=CALLER_ID):
    """
    Acquires a database-based advisory lock for database migrations.
    This context manager handles the acquisition and ensures the lock is released upon exit.

    For SQLite, 'BEGIN EXCLUSIVE' provides a strong database-level lock.
    For other RDBMS (e.g., PostgreSQL, MySQL), a row-level lock using 'SELECT ... FOR UPDATE'
    within a transaction, or specific advisory lock functions (like pg_advisory_lock),
    would be more appropriate and less restrictive on other database operations.
    """
    start_time = time.monotonic()
    logger.info(f"Attempting to acquire migration lock (caller: {caller_id})...")
    
    lock_acquired = False
    try:
        while not lock_acquired:
            cursor = conn.cursor()
            try:
                # Use a transaction for atomic lock acquisition.
                # For SQLite, BEGIN EXCLUSIVE locks the entire database.
                cursor.execute("BEGIN EXCLUSIVE;") 
                
                # Retrieve lock status. For general RDBMS, 'FOR UPDATE' would be appended here
                # to lock the row, e.g., "SELECT ... FROM migration_lock WHERE id = 1 FOR UPDATE;".
                cursor.execute("SELECT is_locked, locked_by, locked_at FROM migration_lock WHERE id = 1;")
                row = cursor.fetchone()

                # The `migration_lock` table and its single row must exist at this point.
                # `initialize_migration_tables` should have guaranteed its presence.
                if row is None:
                    # This indicates a critical state, likely a prior initialization failure or manual corruption.
                    raise MigrationError("Migration lock table row could not be found. "
                                         "Ensure initialize_migration_tables runs successfully first.")

                is_locked, current_locked_by, locked_at = row

                if not is_locked:
                    # Lock is free, acquire it.
                    cursor.execute(
                        "UPDATE migration_lock SET is_locked = TRUE, locked_by = ?, locked_at = ? WHERE id = 1;",
                        (caller_id, datetime.datetime.now())
                    )
                    conn.commit() # Commit the lock acquisition
                    lock_acquired = True
                    logger.info(f"Migration lock acquired by {caller_id}.")
                else:
                    # Lock is held, rollback the current transaction and retry.
                    conn.rollback() 
                    
                    if time.monotonic() - start_time > LOCK_TIMEOUT_SECONDS:
                        # Timeout reached. Report who holds the lock.
                        # Advanced systems might implement "stale lock breaking" based on `locked_at`
                        # but this carries risks and is generally avoided for critical migration locks.
                        raise MigrationError(
                            f"Failed to acquire migration lock within {LOCK_TIMEOUT_SECONDS} seconds. "
                            f"Currently locked by: {current_locked_by if current_locked_by else 'UNKNOWN'} "
                            f"(since {locked_at})."
                        )
                    logger.warning(
                        f"Migration lock is currently held by '{current_locked_by}'. "
                        f"Retrying in {RETRY_INTERVAL_SECONDS}s..."
                    )
                    time.sleep(RETRY_INTERVAL_SECONDS)
            except Exception as e:
                # If an error occurs during a single acquisition attempt, ensure transaction is rolled back.
                if conn.in_transaction: 
                    conn.rollback()
                raise MigrationError(f"Error during lock acquisition attempt: {e}")
        
        # If lock_acquired is True, yield control to the 'with' block where migrations will run.
        yield
    finally:
        # This finally block is executed when exiting the 'with' statement.
        # Ensure the lock is released ONLY if it was successfully acquired by THIS caller.
        if lock_acquired:
            release_migration_lock(conn, caller_id)

def release_migration_lock(conn, caller_id=CALLER_ID):
    """
    Releases the migration lock.
    Only the process that acquired the lock can release it.
    """
    logger.info(f"Attempting to release migration lock by {caller_id}...")
    cursor = conn.cursor()
    try:
        # Start a transaction for the update to ensure atomicity
        cursor.execute("BEGIN;") 
        cursor.execute(
            "UPDATE migration_lock SET is_locked = FALSE, locked_by = NULL, locked_at = NULL WHERE id = 1 AND locked_by = ?;",
            (caller_id,)
        )
        if cursor.rowcount == 0:
            # This means the lock was either not held by this caller, or already released.
            logger.warning(f"Lock for {caller_id} was not released. "
                           f"It might have been already released or held by another caller (rowcount: {cursor.rowcount}).")
            if conn.in_transaction: # Always rollback if no update to prevent committing an empty transaction
                conn.rollback()
        else:
            conn.commit()
            logger.info(f"Migration lock released by {caller_id}.")
    except Exception as e:
        logger.error(f"Failed to release migration lock by {caller_id}: {e}")
        if conn.in_transaction: 
            conn.rollback()

def has_migration_run(conn, migration_name):
    """Checks if a migration has already been applied by looking up its name."""
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM applied_migrations WHERE name = ?", (migration_name,))
    return cursor.fetchone() is not None

def record_migration(conn, migration_name):
    """Records a migration as applied in the 'applied_migrations' table."""
    cursor = conn.cursor()
    try:
        # Start a transaction for the insert to ensure atomicity
        cursor.execute("BEGIN;") 
        cursor.execute("INSERT INTO applied_migrations (name) VALUES (?)", (migration_name,))
        conn.commit()
        logger.info(f"Recorded migration: {migration_name}")
    except sqlite3.IntegrityError:
        # This specific error indicates a unique constraint violation on 'name'.
        # It means the migration was already recorded, possibly due to a race condition
        # if the lock was somehow bypassed or a retry happened after a failed record attempt.
        logger.warning(f"Migration '{migration_name}' already recorded (IntegrityError). Skipping record operation.")
        if conn.in_transaction:
            conn.rollback()
    except Exception as e:
        if conn.in_transaction:
            conn.rollback()
        raise MigrationError(f"Failed to record migration '{migration_name}': {e}")

def get_migration_files(migrations_dir):
    """
    Returns a sorted list of absolute paths to migration SQL files from the specified directory.
    Migrations are sorted alphabetically, assuming a 'YYYYMMDD_NNN_description.sql' naming convention
    to ensure correct execution order.
    """
    migration_files = []
    if not os.path.exists(migrations_dir):
        logger.info(f"Migrations directory '{migrations_dir}' not found. Creating it...")
        os.makedirs(migrations_dir)
        return []

    for filename in sorted(os.listdir(migrations_dir)):
        if filename.endswith(".sql"):
            migration_files.append(os.path.join(migrations_dir, filename))
    return migration_files

def apply_migration_sql(conn, migration_path):
    """
    Applies a single SQL migration file to the database within a transaction.
    
    NOTE: sqlite3.Cursor.executescript() is specific to SQLite and allows executing
    multiple SQL statements separated by semicolons. For other database drivers,
    you would typically need to parse the SQL script into individual statements
    and execute them one by one within the same transaction to ensure portability
    and proper error handling.
    """
    migration_name = os.path.basename(migration_path)
    logger.info(f"Applying migration: {migration_name}...")
    
    try:
        with open(migration_path, 'r') as f:
            sql_script = f.read()
    except IOError as e:
        raise MigrationError(f"Failed to read migration file '{migration_path}': {e}")

    cursor = conn.cursor()
    try:
        # Start a transaction for the migration script to ensure atomicity.
        # If any statement within the script fails, the entire transaction will be rolled back.
        cursor.execute("BEGIN;") 
        cursor.executescript(sql_script) # SQLite specific
        conn.commit()
        logger.info(f"Successfully applied migration: {migration_name}")
    except Exception as e:
        if conn.in_transaction:
            conn.rollback() # Rollback on error to ensure data consistency
        raise MigrationError(f"Error applying migration '{migration_name}': {e}")


def run_migrations():
    """
    Main function to orchestrate and run all pending database migrations.
    It establishes a database connection, initializes migration tables,
    acquires a database-based lock, applies new migrations idempotently,
    and ensures proper resource cleanup.
    """
    conn = None
    try:
        conn = get_db_connection()
        # Ensure migration tables exist before proceeding with lock acquisition or migration checks.
        initialize_migration_tables(conn) 

        # Acquire a global migration lock to prevent concurrent migration runs.
        with acquire_migration_lock(conn):
            migration_files = get_migration_files(MIGRATIONS_DIR)
            if not migration_files:
                logger.info("No migration files found to apply.")
                return

            applied_count = 0
            for migration_file in migration_files:
                migration_name = os.path.basename(migration_file)
                if has_migration_run(conn, migration_name):
                    logger.info(f"Migration '{migration_name}' already applied. Skipping.")
                else:
                    apply_migration_sql(conn, migration_file)
                    record_migration(conn, migration_name)
                    applied_count += 1
            
            if applied_count > 0:
                logger.info(f"Successfully applied {applied_count} new migrations.")
            else:
                logger.info("All migrations already applied.")
    except MigrationError as e:
        logger.error(f"Migration process failed: {e}")
        sys.exit(1)
    except Exception as e:
        # Catch any unexpected critical errors and log them with traceback.
        logger.critical(f"An unexpected critical error occurred during migration: {e}", exc_info=True)
        sys.exit(1)
    finally:
        # Ensure the database connection is closed regardless of success or failure.
        if conn:
            conn.close()
            logger.info("Database connection closed.")