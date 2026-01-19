/**
 * PlexCache-R Web UI JavaScript
 * Shared utilities and HTMX error handling
 */

// Handle HTMX errors
document.addEventListener('htmx:responseError', function(event) {
    const alertContainer = document.getElementById('alert-container');
    if (alertContainer) {
        alertContainer.innerHTML = `
            <article class="alert alert-error">
                Request failed: ${event.detail.xhr.status} ${event.detail.xhr.statusText}
                <button class="close" onclick="this.parentElement.remove()">&times;</button>
            </article>
        `;
    }
});

// Utility: Format file size
function formatFileSize(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

// Utility: Format duration
function formatDuration(seconds) {
    if (seconds < 60) return `${seconds.toFixed(1)}s`;
    const minutes = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${minutes}m ${secs.toFixed(0)}s`;
}
