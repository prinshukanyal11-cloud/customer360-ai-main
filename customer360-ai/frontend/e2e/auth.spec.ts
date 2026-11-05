import { expect, test, type APIRequestContext } from "@playwright/test";
import { API_URL } from "./backend-url";
import { register, storedToken } from "./helpers";

/**
 * Deliberately narrow. The HTTP smoke script already covers the API surface, so
 * duplicating that in a browser would only make this suite slow and flaky.
 * These cases cover the two things that only exist once a browser is involved:
 * the axios refresh interceptor, and whether signing out reaches the server.
 */

/** Call the API directly, bypassing the page. */
function api(request: APIRequestContext) {
  return {
    refresh: (token: string) =>
      request.post(`${API_URL}/auth/refresh`, { data: { refresh_token: token } }),
  };
}

test("registers, lands on the dashboard, and stores a token pair", async ({ page }) => {
  await register(page);

  const access = await storedToken(page, "access_token");
  const refresh = await storedToken(page, "refresh_token");
  expect(refresh).not.toBe(access);
});

test("signing out clears local tokens and revokes the refresh token on the server", async ({
  page,
  request,
}) => {
  await register(page);
  const refresh = await storedToken(page, "refresh_token");

  await page.goto("/settings");
  await page.getByRole("button", { name: /sign out/i }).click();
  await expect(page).toHaveURL(/\/login/);

  // Cleared in the browser...
  await expect
    .poll(() => page.evaluate(() => localStorage.getItem("refresh_token")))
    .toBeNull();

  // ...and no longer honoured by the backend. Without the server-side call this
  // would still return a fresh pair: the token lives on for its full lifetime.
  const res = await api(request).refresh(refresh);
  expect(res.status()).toBe(401);
});

test("a rejected access token is refreshed transparently and the rotated token is stored", async ({
  page,
}) => {
  await register(page);
  const before = await storedToken(page, "refresh_token");

  // Expire the access token without touching the refresh token, which is exactly
  // what the server sees once ACCESS_TOKEN_EXPIRE_MINUTES has elapsed.
  await page.evaluate(() => localStorage.setItem("access_token", "expired.access.token"));

  // Any authenticated request should now 401, trigger one refresh, and retry.
  await Promise.all([
    page.waitForResponse((r) => r.url().includes("/auth/me") && r.status() === 200),
    page.goto("/settings"),
  ]);

  const after = await storedToken(page, "refresh_token");
  // The refresh token is single-use: rotation means the stored value must change.
  expect(after).not.toBe(before);
  expect(await storedToken(page, "access_token")).not.toBe("expired.access.token");
});

test("replaying a spent refresh token kills the whole family and the client stops retrying", async ({
  page,
  request,
}) => {
  await register(page);
  const original = await storedToken(page, "refresh_token");

  // One legitimate refresh: the original is retired and replaced.
  await page.evaluate(() => localStorage.setItem("access_token", "expired.access.token"));
  await Promise.all([
    page.waitForResponse((r) => r.url().includes("/auth/me") && r.status() === 200),
    page.goto("/settings"),
  ]);
  const rotated = await storedToken(page, "refresh_token");
  expect(rotated).not.toBe(original);

  // Put the already-spent token back, as anyone who copied it would have. The
  // backend treats a replay as a possible leak and revokes the whole family, so
  // even the good rotated token dies. The client must give up rather than retry
  // the same dead token and loop.
  await page.evaluate((spent) => {
    localStorage.setItem("access_token", "expired.access.token");
    localStorage.setItem("refresh_token", spent);
  }, original);

  await page.goto("/settings");
  await expect
    .poll(() => page.evaluate(() => localStorage.getItem("refresh_token")), { timeout: 15_000 })
    .toBeNull();

  // And the rotated token is dead server-side too, which is the point of family
  // revocation: replaying one captured token must not buy a working session.
  const res = await api(request).refresh(rotated);
  expect(res.status()).toBe(401);
});