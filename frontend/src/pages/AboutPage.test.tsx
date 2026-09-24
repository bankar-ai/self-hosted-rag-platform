import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import AboutPage from "./AboutPage";
import { CAN_DO, CANNOT_DO } from "../lib/introContent";

describe("AboutPage", () => {
  it("renders every can-do and cannot-do bullet point", () => {
    render(<AboutPage />);
    for (const item of CAN_DO) {
      expect(screen.getByText(item)).toBeInTheDocument();
    }
    for (const item of CANNOT_DO) {
      expect(screen.getByText(item)).toBeInTheDocument();
    }
  });
});
