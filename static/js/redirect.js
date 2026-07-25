/**
 * Page redirect handler
 * Listens for 'redirectTo' custom events and navigates
 */

document.addEventListener('redirectTo', (event) => {
  const { url } = event.detail;
  if (url) {
    window.location.href = url;
  }
});
