import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Button } from "./button";

describe("Button", () => {
  it("defaults to the primary variant (brand background, white text)", () => {
    render(<Button>Click me</Button>);
    const button = screen.getByRole("button", { name: "Click me" });
    expect(button.className).toContain("bg-brand");
    expect(button.className).toContain("text-white");
    expect(button.className).not.toContain("bg-white");
  });

  it("renders the outline variant with no background/text-color overlap with primary", () => {
    render(<Button variant="outline">Retry</Button>);
    const button = screen.getByRole("button", { name: "Retry" });
    expect(button.className).toContain("bg-white");
    expect(button.className).not.toContain("bg-brand");
    expect(button.className).not.toContain("text-white");
  });

  it("renders the danger variant with no background/text-color overlap with primary", () => {
    render(<Button variant="danger">Delete</Button>);
    const button = screen.getByRole("button", { name: "Delete" });
    expect(button.className).toContain("text-red-600");
    expect(button.className).not.toContain("bg-brand");
    expect(button.className).not.toContain("text-white");
  });

  it("still accepts a className for structural overrides alongside any variant", () => {
    render(<Button className="w-full">Full width</Button>);
    expect(screen.getByRole("button", { name: "Full width" }).className).toContain("w-full");
  });
});
