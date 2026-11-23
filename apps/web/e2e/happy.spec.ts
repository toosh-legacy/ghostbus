import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

async function axe(page: import("@playwright/test").Page) {
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
  expect(r.violations.map((v) => `${v.id}: ${v.help}`)).toEqual([]);
}

test("nearby -> stop page -> route map -> reliability -> copilot (mobile)", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /actually coming/ })).toBeVisible();
  await axe(page);
  await page.getByLabel("Stop name or route").fill("Nubian");
  await page.getByRole("button", { name: "Search" }).click();
  await page.getByRole("list", { name: "Search results" }).getByRole("link").first().click();
  await expect(page.getByRole("list", { name: "Upcoming buses" })).toBeVisible();
  await axe(page);
  await page.goto("/routes/1");
  await expect(page.getByRole("heading", { name: /Buses on this route/ })).toBeVisible();
  await page.goto("/reliability");
  await expect(page.getByRole("table")).toBeVisible();
  await axe(page);
  await page.goto("/ask");
  await page.getByRole("button", { name: "Ask" }).click();
  await expect(page.getByText(/Highest missed-trip rate/)).toBeVisible();
  await axe(page);
  await page.goto("/methodology");
  await expect(page.getByText("ghost-def v1.0").first()).toBeVisible();
  await axe(page);
});
