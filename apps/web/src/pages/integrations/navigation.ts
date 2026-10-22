/**
 * Leaves the app for a provider's sign-in page. It lives in its own module so that tests can replace
 * the browser redirect with a mock. Callers pass only an https address that the server returned.
 */
export function assignLocation(url: string): void {
  window.location.assign(url);
}
