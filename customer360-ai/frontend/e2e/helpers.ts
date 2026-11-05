import { expect, type Page } from "@playwright/test";

export const PASSWORD = "Str0ngPassw0rd!";

export function uniqueEmail(prefix: string): string {
  // example.com, not example.test: the backend's EmailStr rejects special-use
  // domains, so a .test address would fail validation before reaching any logic.
  return `${prefix}-${Date.now()}-${Math.floor(Math.random() * 10_000)}@example.com`;
}

/** Read a token from localStorage, failing loudly if it is absent. */
export async function storedToken(page: Page, key: string): Promise<string> {
  const value = await page.evaluate((k) => localStorage.getItem(k), key);
  if (!value) {
    throw new Error(`Expected ${key} to be set in localStorage.`);
  }
  return value;
}

/** Register through the real form and wait for the dashboard. */
export async function register(page: Page): Promise<string> {
  const email = uniqueEmail("user");
  await page.goto("/register");
  await page.getByLabel("Full name").fill("E2E User");
  await page.getByLabel("Organization name").fill(`E2E Org ${Date.now()}`);
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: /create|sign up|register/i }).click();
  await expect(page).toHaveURL(/\/(dashboard)?$/);
  return email;
}