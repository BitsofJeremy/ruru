/**
 * Toast notification system
 * Listens for 'showToast' custom events and displays notifications
 */

function createToast(message, type = 'info', autoDismissMs = null) {
  const container = document.getElementById('toast-container');

  const toast = document.createElement('div');
  toast.className = `toast ${type}`;

  // Build content
  let content = message;
  if (type === 'error') {
    content += ' <span class="toast-close">✕</span>';
  }
  toast.innerHTML = content;

  // Add close button listener for errors
  if (type === 'error') {
    const closeBtn = toast.querySelector('.toast-close');
    closeBtn.addEventListener('click', () => removeToast(toast));
  }

  container.appendChild(toast);

  // Auto-dismiss if specified
  if (autoDismissMs) {
    setTimeout(() => removeToast(toast), autoDismissMs);
  }

  return toast;
}

function removeToast(toast) {
  toast.classList.add('removing');
  setTimeout(() => {
    toast.remove();
  }, 300); // Match slideOut animation duration
}

// Listen for showToast custom event (dispatched by HX-Trigger)
document.addEventListener('showToast', (event) => {
  const { message, type = 'info' } = event.detail;

  // Auto-dismiss timing: success/info = 4s, error = no auto-dismiss
  const autoDismissMs = (type === 'error') ? null : 4000;

  createToast(message, type, autoDismissMs);
});
