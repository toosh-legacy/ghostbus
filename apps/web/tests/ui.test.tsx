import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { GhostBadge, StaleBanner } from "@/components/ui";
import { pct, routeLabel } from "@/lib/api";

describe("GhostBadge", () => {
  it("never relies on colour alone: icon, text label and reason", () => {
    render(<GhostBadge state="high" reason="no vehicle matched" prior={0.123} />);
    expect(screen.getByText("High ghost risk")).toBeInTheDocument();
    expect(screen.getByTitle("no vehicle matched")).toBeInTheDocument();
    expect(screen.getByText(/prior 12\.3%/)).toBeInTheDocument();
  });
  it("falls back to unknown for unexpected states", () => {
    render(<GhostBadge state="weird" />);
    expect(screen.getByText("Ghost risk unknown")).toBeInTheDocument();
  });
});

describe("StaleBanner", () => {
  it("renders an alert only when the feed is unhealthy", () => {
    const { container, rerender } = render(<StaleBanner health={{ healthy: true } as never} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<StaleBanner health={{ healthy: false, reason: "feed outage", stale: true } as never} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Live data is stale");
  });
});

describe("formatters", () => {
  it("formats rates and Silver Line labels", () => {
    expect(pct(0.1234)).toMatch(/12\.3/);
    expect(routeLabel("741")).toMatch(/SL1/);
  });
});
