import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import IntroModal from "./IntroModal";

describe("IntroModal", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    localStorage.clear();
  });

  it("shows on first render when the localStorage flag is unset", () => {
    render(
      <MemoryRouter>
        <IntroModal />
      </MemoryRouter>
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("does not show when the localStorage flag is already set", () => {
    localStorage.setItem("introSeen_v1", "true");
    render(
      <MemoryRouter>
        <IntroModal />
      </MemoryRouter>
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("dismisses and sets the flag when closed", async () => {
    render(
      <MemoryRouter>
        <IntroModal />
      </MemoryRouter>
    );
    await userEvent.click(screen.getByRole("button", { name: /got it/i }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(localStorage.getItem("introSeen_v1")).toBe("true");
  });
});
