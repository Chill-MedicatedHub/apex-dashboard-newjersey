// index.js — Basic Auth gate in front of the static dashboard.
//
// This turns the Worker from "assets only" into "Worker + assets", which is
// what lets you attach secrets and password-protect the page.
//
// Set AUTH_USER and AUTH_PASS as ENCRYPTED SECRETS (never plaintext vars):
//   npx wrangler secret put AUTH_USER
//   npx wrangler secret put AUTH_PASS
// or in the Cloudflare dashboard → your Worker → Settings → Variables and secrets
// → Add → type "Secret" (that option appears once this script is deployed).
export default {
  async fetch(request, env) {
    if (!env.AUTH_USER || !env.AUTH_PASS) {
      return new Response("Auth not configured — set AUTH_USER and AUTH_PASS secrets.", { status: 500 });
    }
    const expected = "Basic " + btoa(`${env.AUTH_USER}:${env.AUTH_PASS}`);
    const provided = request.headers.get("Authorization") || "";
    if (provided !== expected) {
      return new Response("Authentication required.", {
        status: 401,
        headers: { "WWW-Authenticate": 'Basic realm="Chill Medicated", charset="UTF-8"' }
      });
    }
    // Authenticated → serve the static dashboard.
    return env.ASSETS.fetch(request);
  }
};
