import { expect, test } from "@playwright/test";
import { register } from "./helpers";

/**
 * Browser-level proof that a real file reaches the server as multipart.
 *
 * This exists because the bug was invisible to the API smoke script: the backend
 * and the endpoint were both fine, but axios flattened the FormData into
 * `{"file":{}}` and FastAPI answered 422 before anything was written. That only
 * reproduces with a real browser talking to a real server.
 */

const CSV = [
  "external_customer_id,name,email,signup_date,location,age",
  "C-1,Alice Smith,alice@example.com,2024-01-15,London,34",
  "C-2,Bob Jones,bob@example.com,2024-03-02,Leeds,41",
].join("\n");

test("uploads a CSV and opens the mapping step", async ({ page }) => {
  await register(page);
  await page.goto("/data/upload");

  await page.locator("#csv").setInputFiles({
    name: "e2e-customers.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(CSV, "utf8"),
  });

  // The picker only records the file locally; this proves the change fired.
  await expect(page.getByText("e2e-customers.csv")).toBeVisible();

  const uploaded = page.waitForResponse(
    (r) => r.url().includes("/datasets/upload") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Upload & inspect" }).click();
  const response = await uploaded;

  // 422 "Field required" was the original symptom of the encoding bug.
  expect(response.status(), await response.text()).toBe(201);

  await expect(page.getByRole("heading", { name: "Dataset summary" })).toBeVisible();
  await expect(page.getByRole("heading", { name: /2 · Map columns/ })).toBeVisible();
  await expect(page.locator('[role="alert"]')).toHaveCount(0);

  // Two data rows parsed end to end, so the bytes crossed the wire rather than
  // a serialised stand-in. This is what the old `{"file":{}}` body could not do.
  await expect(page.locator("dt:text-is('Rows') + dd")).toHaveText("2");
});
