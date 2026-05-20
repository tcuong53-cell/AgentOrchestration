import React, { useState, useEffect, useCallback } from 'react';

// --- Mock API Service ---
// In a real application, this would be a properly configured API client (e.g., Axios instance, Fetch API wrapper)
// handling authentication, error handling, and base URL configuration.
// For demonstration, a mock AUTH_TOKEN is used. In a real scenario, this would come from a secure auth context.
const MOCK_AUTH_TOKEN = "your_secure_auth_token_here"; // Placeholder

const apiService = {
    get: async (url) => {
        await new Promise(resolve => setTimeout(resolve, 300)); // Simulate network delay
        const response = await fetch(url, {
            headers: {
                'Authorization': `Bearer ${MOCK_AUTH_TOKEN}`,
                'Content-Type': 'application/json',
            },
        });
        if (!response.ok) {
            const errorBody = await response.json().catch(() => ({ message: response.statusText }));
            throw new Error(`HTTP error! Status: ${response.status}, Details: ${errorBody.message || 'No additional details.'}`);
        }
        return response.json();
    },
    post: async (url, data) => {
        await new Promise(resolve => setTimeout(resolve, 300)); // Simulate network delay
        const response = await fetch(url, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${MOCK_AUTH_TOKEN}`, // Include Authorization for audited access
            },
            body: JSON.stringify(data),
        });
        if (!response.ok) {
            const errorBody = await response.json().catch(() => ({ message: response.statusText }));
            throw new Error(`HTTP error! Status: ${response.status}, Details: ${errorBody.message || 'No additional details.'}`);
        }
        return response.json();
    }
};
// --- End Mock API Service ---


/**
 * Client-side function to redact payload excerpts for default display.
 * This acts as a secondary layer of defense, even if the backend `/summary`
 * endpoint tries to be careful. It aims to avoid displaying PII/sensitive info.
 *
 * IMPORTANT: Primary redaction should always happen on the backend, close to the data source.
 * This client-side function is a safeguard for displaying summaries and should not be relied
 * upon as the sole security measure.
 *
 * @param {any} payload - The payload data (can be object, string, number, etc.)
 * @returns {string} Redacted string for display.
 */
const redactPayloadForDisplay = (payload) => {
    if (payload === null || payload === undefined) return "[No Payload Available]";

    let payloadStr;
    try {
        // Attempt to stringify objects for consistent display. Handle primitives directly.
        payloadStr = typeof payload === 'object' ? JSON.stringify(payload, null, 2) : String(payload);
    } catch (e) {
        // Fallback if JSON.stringify fails (e.g., circular structures)
        console.warn("Failed to stringify payload for redaction:", e);
        payloadStr = String(payload);
    }

    let redactedStr = payloadStr;

    // Apply more specific redaction for known sensitive keys/patterns (case-insensitive)
    // This targets common key-value pairs in JSON-like structures
    redactedStr = redactedStr.replace(
        /"(password|token|secret|apiKey|api_key|privateKey|ssn|creditCard|card_number|cvv|securityCode|auth_code)":\s*"(.*?)"/gi,
        `"$1": "[REDACTED]"`
    );
    // This targets common authorization header values or similar patterns
    redactedStr = redactedStr.replace(
        /(bearer|x-api-key|authorization|api-key)\s*[:=]\s*['"]?(.*?)(['"]|$)/gi,
        `$1: "[REDACTED]"`
    );
    // Generic redaction for values that look like UUIDs, JWTs, or long base64 strings if not tied to a key
    redactedStr = redactedStr.replace(
        /\b([A-Za-z0-9+/=]{20,})\b(?![^"]*")?/g, // Matches long alphanumeric strings that might be tokens/keys, avoids if inside quoted string key
        (match, p1) => {
            // A simple heuristic: if it contains '/', '+', or '=', it's likely base64.
            // If it's very long and mixed case, could be a token.
            // This is a heuristic and can be refined.
            if (p1.length > 30 && (p1.includes('/') || p1.includes('+') || p1.includes('='))) {
                 return "[REDACTED_VALUE]";
            }
            return match; // Don't redact if heuristic isn't met
        }
    );


    // Truncate long payloads *after* redaction to limit the excerpt size for display.
    const MAX_EXCERPT_LENGTH = 500;
    if (redactedStr.length > MAX_EXCERPT_LENGTH) {
        redactedStr = redactedStr.substring(0, MAX_EXCERPT_LENGTH) + "\n\n... [Payload excerpt truncated for brevity] ...";
    }

    return redactedStr + "\n\n[This is a redacted summary for routine viewing. Access to raw data is audited.]";
};


/**
 * Component for viewing dead-letter queue messages.
 * By default, it displays a redacted summary of the payload.
 * Full raw payload access requires an explicit user action, which is logged and audited.
 */
const DeadLetterQueueViewer = ({ taskId }) => {
    const [taskMessage, setTaskMessage] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [showRawPayload, setShowRawPayload] = useState(false); // State to control raw vs redacted view
    const [auditReason, setAuditReason] = useState(''); // State to hold user-provided audit reason

    const fetchTaskMessageSummary = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            // By default, fetch a summarized or pre-redacted version of the message from the backend.
            // The backend endpoint `/summary` should explicitly return non-sensitive data,
            // or a truncated payload that avoids exposing PII.
            const data = await apiService.get(`/api/dead-letter-queue/${taskId}/summary`);
            setTaskMessage(data);
        } catch (err) {
            console.error("Failed to fetch task message summary:", err);
            setError(`Failed to load task message summary: ${err.message || 'Please check network or try again.'}`);
        } finally {
            setLoading(false);
        }
    }, [taskId]);

    useEffect(() => {
        fetchTaskMessageSummary();
    }, [fetchTaskMessageSummary]); // Depend on memoized fetch function

    const handleViewRawPayload = async () => {
        const reason = prompt("WARNING: Accessing the full raw payload is an audited action and will be logged. This may expose sensitive data. Please provide a brief reason for access (e.g., 'debugging user task failure'):");

        if (reason) { // Only proceed if a reason is provided
            setAuditReason(reason); // Store the reason for potential display or context
            setLoading(true);
            setError(null);
            try {
                // Request the raw payload from a dedicated backend endpoint.
                // This backend endpoint (`/raw`) *must* handle:
                // 1. Elevated authorization checks (e.g., specific roles, 2FA if applicable).
                // 2. Logging this specific request as an audit event with user, timestamp, and provided reason.
                // 3. Returning the full, unredacted payload only if authorized and logged.
                const data = await apiService.post(`/api/dead-letter-queue/${taskId}/raw`, {
                    action: 'view_raw_payload',
                    reason: reason, // Send the user-provided reason for audit log
                    // User ID is typically derived from the Authorization header by the backend.
                });
                // Update the taskMessage with the full raw payload received
                setTaskMessage(prev => ({ ...prev, payload: data.payload, payloadSource: 'raw' })); // Add payloadSource for clarity
                setShowRawPayload(true); // Switch to display raw data
            } catch (err) {
                console.error("Failed to fetch raw payload or log access:", err);
                setError(`Failed to retrieve raw payload or log access: ${err.message || 'Ensure you have necessary elevated permissions.'}`);
            } finally {
                setLoading(false);
            }
        } else if (reason === null) {
            // User cancelled the prompt
            console.log("Raw payload access request cancelled by user.");
        }
    };


    if (loading) return <div style={styles.loadingContainer}>Loading task details...</div>;
    if (error) return <div style={styles.errorContainer}><strong>Error:</strong> {error}</div>;
    if (!taskMessage) return <div style={styles.noMessageContainer}>No dead-letter message found for ID: {taskId}.</div>;

    // Determine what content to display in the payload section
    const currentPayloadContent = showRawPayload
        ? (typeof taskMessage.payload === 'object' ? JSON.stringify(taskMessage.payload, null, 2) : String(taskMessage.payload))
        : redactPayloadForDisplay(taskMessage.payload); // Apply client-side redaction to the summary payload

    const payloadBoxStyle = {
        ...styles.payloadBox,
        backgroundColor: showRawPayload ? '#fff3cd' : '#f8f8f8', // Highlight raw data differently
        border: showRawPayload ? '1px solid #ffc107' : '1px solid #e0e0e0',
    };

    return (
        <div className="dead-letter-queue-viewer" style={styles.viewerContainer}>
            <h2 style={styles.viewerTitle}>Dead-Letter Message: Task ID {taskId}</h2>

            <div style={styles.detailRow}>
                <strong style={styles.detailLabel}>Status:</strong>
                <span style={{ ...styles.detailValue, color: taskMessage.status === 'FAILED' ? '#dc3545' : '#28a745', fontWeight: 'bold' }}>
                    {taskMessage.status || 'N/A'}
                </span>
            </div>
            <div style={styles.detailRow}>
                <strong style={styles.detailLabel}>Error Type:</strong>
                <span style={styles.detailValue}>{taskMessage.errorType || 'N/A'}</span>
            </div>
            <div style={styles.detailRow}>
                <strong style={styles.detailLabel}>Timestamp:</strong>
                <span style={styles.detailValue}>{taskMessage.timestamp ? new Date(taskMessage.timestamp).toLocaleString() : 'N/A'}</span>
            </div>
            <div style={styles.detailRow}>
                <strong style={styles.detailLabel}>Summary/Reason:</strong>
                <span style={styles.detailValueFlex}>{taskMessage.summary || '[No specific summary provided]'}</span>
            </div>

            <h3 style={styles.payloadTitle}>Payload Data {showRawPayload && auditReason && <span style={styles.auditReasonDisplay}>(Accessed raw data for: "{auditReason}")</span>}</h3>
            <pre style={payloadBoxStyle}>
                {currentPayloadContent}
            </pre>

            {!showRawPayload && (
                <button
                    onClick={handleViewRawPayload}
                    style={styles.viewRawButton}
                    onMouseOver={(e) => e.currentTarget.style.backgroundColor = styles.viewRawButtonHover.backgroundColor}
                    onMouseOut={(e) => e.currentTarget.style.backgroundColor = styles.viewRawButton.backgroundColor}
                    onMouseDown={(e) => e.currentTarget.style.transform = styles.viewRawButtonActive.transform}
                    onMouseUp={(e) => e.currentTarget.style.transform = styles.viewRawButton.transform}
                >
                    🚨 View Full Raw Payload (Audited Access Required) 🚨
                </button>
            )}
        </div>
    );
};

// Centralized styles for better organization and readability
const styles = {
    viewerContainer: {
        fontFamily: 'Arial, sans-serif',
        maxWidth: '900px',
        margin: '20px auto',
        padding: '25px',
        border: '1px solid #ddd',
        borderRadius: '10px',
        boxShadow: '0 2px 8px rgba(0,0,0,0.1)'
    },
    viewerTitle: {
        borderBottom: '2px solid #eee',
        paddingBottom: '10px',
        marginBottom: '25px',
        color: '#333'
    },
    detailRow: {
        marginBottom: '15px',
        display: 'flex',
        alignItems: 'flex-start' // Align items to top in case of multi-line reason
    },
    detailLabel: {
        minWidth: '120px',
        color: '#555',
        marginRight: '10px'
    },
    detailValue: {
        color: '#333'
    },
    detailValueFlex: {
        flexGrow: 1,
        color: '#333'
    },
    payloadTitle: {
        borderBottom: '1px solid #eee',
        paddingBottom: '8px',
        marginBottom: '15px',
        color: '#333',
        display: 'flex',
        alignItems: 'center',
        gap: '10px'
    },
    auditReasonDisplay: {
        fontSize: '0.85em',
        color: '#777',
        fontStyle: 'italic',
        fontWeight: 'normal',
        backgroundColor: '#e9ecef',
        padding: '3px 8px',
        borderRadius: '5px'
    },
    payloadBox: {
        padding: '18px',
        borderRadius: '8px',
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-all',
        maxHeight: '400px',
        overflowY: 'auto',
        fontSize: '0.95em',
        lineHeight: '1.4'
    },
    viewRawButton: {
        marginTop: '25px',
        padding: '14px 30px',
        backgroundColor: '#dc3545', // Red color for sensitive action
        color: 'white',
        border: 'none',
        borderRadius: '7px',
        cursor: 'pointer',
        fontSize: '1.05em',
        fontWeight: 'bold',
        transition: 'background-color 0.3s ease, transform 0.2s ease',
        boxShadow: '0 4px 10px rgba(0,0,0,0.15)',
        display: 'block',
        width: 'fit-content',
        margin: '25px auto 0 auto',
        transform: 'translateY(0)' // Ensure initial state matches
    },
    viewRawButtonHover: {
        backgroundColor: '#c82333'
    },
    viewRawButtonActive: {
        transform: 'translateY(1px)'
    },
    loadingContainer: {
        textAlign: 'center',
        padding: '20px',
        fontSize: '1.1em'
    },
    errorContainer: {
        color: '#8b0000',
        backgroundColor: '#ffe0e0',
        padding: '15px',
        borderRadius: '8px',
        border: '1px solid #ffb3b3',
        margin: '20px auto',
        maxWidth: '800px'
    },
    noMessageContainer: {
        textAlign: 'center',
        padding: '20px',
        color: '#666',
        fontSize: '1.1em'
    }
};

export default DeadLetterQueueViewer;